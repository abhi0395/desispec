"""
desispec.scripts.proc_night
=============================

"""
from desispec.io import findfile
from desispec.workflow.calibration_selection import \
    determine_calibrations_to_proc
from desispec.workflow.science_selection import determine_science_to_proc, \
    get_tiles_cumulative
from desiutil.log import get_logger
import numpy as np
import os
import sys
import time
import re
from astropy.table import Table, vstack

## Import some helper functions, you can see their definitions by uncomenting the bash shell command
from desispec.scripts.update_exptable import update_exposure_table
from desispec.workflow.tableio import load_tables, write_table
from desispec.workflow.utils import sleep_and_report, \
    verify_variable_with_environment, load_override_file
from desispec.workflow.timing import what_night_is_it, during_operating_hours
from desispec.workflow.exptable import get_last_step_options
from desispec.workflow.proctable import default_obstypes_for_proctable, \
    erow_to_prow, default_prow, table_row_to_dict
from desispec.workflow.processing import define_and_assign_dependency, \
                                         create_and_submit, \
                                         submit_tilenight_and_redshifts, \
                                         generate_calibration_dict, \
                                         night_to_starting_iid, make_joint_prow, \
                                         set_calibrator_flag, make_exposure_prow
from desispec.workflow.queue import update_from_queue, any_jobs_not_complete
from desispec.io.util import decode_camword, difference_camwords, \
    create_camword, replace_prefix


def proc_night(night=None, proc_obstypes=None, z_submit_types=None,
               queue='realtime', reservation=None, system_name=None,
               exp_table_pathname=None, proc_table_pathname=None,
               override_pathname=None,
               dry_run_level=0, dry_run=False, no_redshifts=False,
               ignore_proc_table_failures = False,
               dont_check_job_outputs=False, dont_resubmit_partial_jobs=False,
               tiles=None, surveys=None, science_laststeps=None,
               all_tiles=False, specstatus_path=None, use_specter=False,
               do_cte_flats=False, complete_tiles_thrunight=None,
               all_cumulatives=False,
               daily=False, specprod=None, path_to_data=None, exp_obstypes=None,
               camword=None, badcamword=None, badamps=None,
               exps_to_ignore=None, exp_cadence_time=0.5, verbose=False,
               dont_require_cals=False):
    """
    Process some or all exposures on a night. Can be used to process an entire
    night, or used to process data currently available on a given night using
    the '--daily' flag.

    Args:
        night (int): The night of data to be processed. Exposure table must exist.
        proc_obstypes (list or np.array, optional): A list of exposure OBSTYPE's
            that should be processed (and therefore added to the processing table).
        z_submit_types (list of str or comma-separated list of str, optional):
            The "group" types of redshifts that should be submitted with each
            exposure. If not specified, default for daily processing is
            ['cumulative', 'pernight-v0']. If false, 'false', or [], then no
            redshifts are submitted.
        queue (str, optional): The name of the queue to submit the jobs to.
            Default is "realtime".
        reservation (str, optional): The reservation to submit jobs to.
            If None, it is not submitted to a reservation.
        system_name (str): batch system name, e.g. cori-haswell, cori-knl,
            perlmutter-gpu
        exp_table_pathname (str): Full path to where to exposure tables are stored,
            including file name.
        proc_table_pathname (str): Full path to where to processing tables to be
            written, including file name
        override_pathname (str): Full path to the override file.
        dry_run_level (int, optional): If nonzero, this is a simulated run.
            If dry_run=1 the scripts will be written but not submitted.
            If dry_run=2, the scripts will not be written nor submitted.
            Logging will remain the same for testing as though scripts are
            being submitted. Default is 0 (false).
        dry_run (bool, optional): When to run without submitting scripts or
            not. If dry_run_level is defined, then it over-rides this flag.
            dry_run_level not set and dry_run=True, dry_run_level is set to 2
            (no scripts generated or run). Default for dry_run is False.
        no_redshifts (bool, optional): Whether to submit redshifts or not.
            If True, redshifts are not submitted.
        ignore_proc_table_failures (bool, optional): True if you want to submit
            other jobs even the loaded processing table has incomplete jobs in
            it. Use with caution. Default is False.
        dont_check_job_outputs (bool, optional): Default is False. If False,
            the code checks for the existence of the expected final data
            products for the script being submitted. If all files exist and
            this is False, then the script will not be submitted. If some
            files exist and this is False, only the subset of the cameras
            without the final data products will be generated and submitted.
        dont_resubmit_partial_jobs (bool, optional): Default is False. Must be
            used with dont_check_job_outputs=False. If this flag is False, jobs
            with some prior data are pruned using PROCCAMWORD to only process
            the remaining cameras not found to exist.
        tiles (array-like, optional): Only submit jobs for these TILEIDs.
        surveys (array-like, optional): Only submit science jobs for these
            surveys (lowercase)
        science_laststeps (array-like, optional): Only submit jobs for exposures
            with LASTSTEP in these science_laststeps (lowercase)
        all_tiles (bool, optional): Default is False. Set to NOT restrict to
            completed tiles as defined by the table pointed to by specstatus_path.
        specstatus_path (str, optional): Default is
            $DESI_SURVEYOPS/ops/tiles-specstatus.ecsv. Location of the
            surveyops specstatus table.
        use_specter (bool, optional): Default is False. If True, use specter,
            otherwise use gpu_specter by default.
        do_cte_flats (bool, optional): Default is True. If True, cte flats
            are used if available to correct for cte effects.
        complete_tiles_thrunight (int, optional): Default is None. Only tiles
            completed on or before the supplied YYYYMMDD are considered
            completed and will be processed. All complete tiles are submitted
            if None or all_tiles is True.
        all_cumulatives (bool, optional): Default is False. Set to run
            cumulative redshifts for all tiles even if the tile has observations
            on a later night.
        specprod: str. The name of the current production. If used, this will
            overwrite the SPECPROD environment variable.
        daily: bool. Flag that sets other flags for running this script for the
            daily pipeline.
        path_to_data: str. Path to the raw data.
        exp_obstypes: str or comma separated list of strings. The exposure
            OBSTYPE's that you want to include in the exposure table.
        camword: str. Camword that, if set, alters the set of cameras that will
            be set for processing. Examples: a0123456789, a1, a2b3r3,
            a2b3r4z3. Note this is only true for new exposures being
            added to the exposure_table in 'daily' mode.
        badcamword: str. Camword that, if set, will be removed from the camword
            defined in camword if given, or the camword inferred from
            the data if camword is not given. Note this is only true
            for new exposures being added to the exposure_table
            in 'daily' mode.
        badamps: str. Comma seperated list of bad amplifiers that should not
            be processed. Should be of the form "{camera}{petal}{amp}",
            i.e. "[brz][0-9][ABCD]". Example: 'b7D,z8A'. Note this is
            only true for new exposures being added to the
            exposure_table in 'daily' mode.
        exp_cadence_time: int. Wait time in seconds between loops over each
            science exposure. Default 0.5 seconds.
        verbose: bool. True if you want more verbose output, false otherwise.
            Current not propagated to lower code, so it is only used in the
            main daily_processing script itself.
        dont_require_cals: bool. Default False. If set then the code doesn't
            require either a valid set of calibrations or a valid override file
            to link to calibrations in order to proceed with science processing.
    """
    ## Get logger
    log = get_logger()

    ## Inform user of how some parameters will be used
    if camword is not None:
        log.info(f"Note custom {camword=} will only be used for new exposures"
                 f" being entered into the exposure_table, not all exposures"
                 f" to be processed.")
    if badcamword is not None:
        log.info(f"Note custom {badcamword=} will only be used for new exposures"
                 f" being entered into the exposure_table, not all exposures"
                 f" to be processed.")
    if badamps is not None:
        log.info(f"Note custom {badamps=} will only be used for new exposures"
                 f" being entered into the exposure_table, not all exposures"
                 f" to be processed.")

    ## Set a flag to determine whether to process the last tile in the exposure table
    ## or not. This is used in daily mode when processing and exiting mid-night.
    ignore_last_tile = False

    ## If running in daily mode, change a bunch of defaults
    if daily:
        ## What night are we running on?
        true_night = what_night_is_it()
        if night is not None:
            night = int(night)
            if true_night != night:
                log.info(f"True night is {true_night}, but running for {night=}")
        else:
            night = true_night

        if during_operating_hours(dry_run=dry_run) and (true_night == night):
            ignore_last_tile = True

        update_exptable = True
        append_to_proc_table = True
        all_cumulatives = True
        all_tiles = True
        complete_tiles_thrunight = None

    ## Set night
    if night is None:
        err = "Must specify night unless running in daily=True mode"
        log.error(err)
        raise ValueError(err)
    else:
        log.info(f"Processing {night=}")

    ## Recast booleans from double negative
    check_for_outputs = (not dont_check_job_outputs)
    resubmit_partial_complete = (not dont_resubmit_partial_jobs)
    require_cals = (not dont_require_cals)

    ###################
    ## Set filenames ##
    ###################
    ## Ensure specprod is set in the environment and that it matches user
    ## specified value if given
    specprod = verify_variable_with_environment(specprod, var_name='specprod',
                                                env_name='SPECPROD')

    ## Determine where the exposure table will be written
    if exp_table_pathname is None:
        exp_table_pathname = findfile('exposure_table', night=night)
    if not os.path.exists(exp_table_pathname) and not update_exptable:
        raise IOError(f"Exposure table: {exp_table_pathname} not found. Exiting this night.")

    ## Determine where the processing table will be written
    if proc_table_pathname is None:
        proc_table_pathname = findfile('processing_table', night=night)
    proc_table_path = os.path.dirname(proc_table_pathname)
    os.makedirs(proc_table_path, exist_ok=True)

    ## Determine where the unprocessed data table will be written
    unproc_table_pathname = replace_prefix(proc_table_pathname, 'processing', 'unprocessed')

    ## Require cal_override to exist if explcitly specified
    if override_pathname is None:
        override_pathname = findfile('override', night=night)
    elif not os.path.exists(override_pathname):
        raise IOError(f"Specified override file: "
                      f"{override_pathname} not found. Exiting this night.")

    #######################################
    ## Define parameters based on inputs ##
    #######################################
    ## If science_laststeps not defined, default is only LASTSTEP=='all' exposures
    if science_laststeps is None:
        science_laststeps = ['all']
    else:
        laststep_options = get_last_step_options()
        for laststep in science_laststeps:
            if laststep not in laststep_options:
                raise ValueError(f"Couldn't understand laststep={laststep} "
                                 + f"in science_laststeps={science_laststeps}.")
    log.info(f"Processing exposures with the following LASTSTEP's: {science_laststeps}")

    ## Define the group types of redshifts you want to generate for each tile
    if no_redshifts:
        log.info(f"no_redshifts set, so ignoring {z_submit_types=}")
        z_submit_types = None

    if z_submit_types is None:
        log.info("Not submitting scripts for redshift fitting")
    else:
        for ztype in z_submit_types:
            if ztype not in ['cumulative', 'pernight-v0', 'pernight', 'perexp']:
                raise ValueError(f"Couldn't understand ztype={ztype} "
                                 + f"in z_submit_types={z_submit_types}.")
        log.info(f"Redshift fitting with redshift group types: {z_submit_types}")

    ## Reconcile the dry_run and dry_run_level
    if dry_run and dry_run_level == 0:
        dry_run_level = 2
    elif dry_run_level > 0:
        dry_run = True

    ## Identify OBSTYPES to process
    if proc_obstypes is None:
        proc_obstypes = default_obstypes_for_proctable()

    #############################
    ## Start the Actual Script ##
    #############################
    ## If running in daily mode, or requested, then update the exposure table
    ## This reads in and writes out the exposure table to disk
    if update_exptable:
        update_exposure_table(night=night, specprod=specprod,
                              exp_table_path=exp_table_pathname,
                              path_to_data=path_to_data, exp_obstypes=exp_obstypes,
                              camword=camword, badcamword=badcamword, badamps=badamps,
                              exps_to_ignore=exps_to_ignore,
                              dry_run_level=dry_run_level, verbose=verbose)

    ## Combine the table names and types for easier passing to io functions
    table_pathnames = [exp_table_pathname, proc_table_pathname]
    table_types = ['exptable', 'proctable']

    ## Load in the files defined above
    etable, ptable = load_tables(tablenames=table_pathnames, tabletypes=table_types)
    full_etable = etable.copy()

    ## Cut on OBSTYPES
    log.info(f"Processing the following obstypes: {proc_obstypes}")
    good_types = np.isin(np.array(etable['OBSTYPE']).astype(str), proc_obstypes)
    etable = etable[good_types]

    ## Update processing table
    tableng = len(ptable)
    if tableng > 0:
        ptable = update_from_queue(ptable, dry_run=dry_run_level)
        if dry_run_level < 3:
            write_table(ptable, tablename=proc_table_pathname)
        if any_jobs_not_complete(ptable['STATUS']):
            if not ignore_proc_table_failures:
                log.error("Some jobs have an incomplete job status. This script "
                      + "will not fix them. You should remedy those first. "
                      + "To proceed anyway use '--ignore-proc-table-failures'. Exiting.")
                raise AssertionError
            else:
                log.warning("Some jobs have an incomplete job status, but "
                      + "you entered '--ignore-proc-table-failures'. This "
                      + "script will not fix them. "
                      + "You should have fixed those first. Proceeding...")
        ptable_expids = set(np.unique(np.concatenate(
                                ptable['EXPID'][ptable['OBSTYPE']=='science']
                            )))
        etable_expids = set(etable['EXPID'][ptable['OBSTYPE']=='science'])
        if len(etable_expids.difference(ptable_expids)) == 0:
            log.info("All science EXPID's already present in processing table, "
                     + "nothing to run. Exiting")
            return ptable
        int_id = np.max(ptable['INTID'])+1

    else:
        int_id = night_to_starting_iid(night=night)

    ################### Determine What to Process ###################
    ## Load calibration_override_file
    overrides = load_override_file(filepathname=override_pathname)
    cal_override = {}
    if 'calibration' in overrides:
        cal_override = overrides['calibration']

    ## Generate the calibration dictionary
    calibjobs = generate_calibration_dict(ptable)

    ## Determine the appropriate set of calibrations
    ## Only run if we haven't already linked or done fiberflatnight's
    cal_etable = None
    if calibjobs['ccdlink'] is None and calibjobs['nightlyflat'] is None:
        cal_etable = determine_calibrations_to_proc(etable,
                                                    do_cte_flats=do_cte_flats,
                                                    still_acquiring=ignore_last_tile)

    ## Determine the appropriate science exposures
    sci_etable, tiles_to_proc = determine_science_to_proc(
                                        etable=etable, tiles=tiles,
                                        surveys=surveys, laststeps=science_laststeps,
                                        processed_tiles=np.unique(ptable['TILEID']),
                                        all_tiles=all_tiles,
                                        ignore_last_tile=ignore_last_tile,
                                        complete_tiles_thrunight=complete_tiles_thrunight,
                                        specstatus_path=specstatus_path)

    ## For cumulative redshifts, identify tiles for which this is the last
    ## night that they were observed
    tiles_cumulative = get_tiles_cumulative(sci_etable, z_submit_types,
                                            all_cumulatives, night)

    ################### Process the data ###################
    ## Process Calibrations
    ## For now assume that a linkcal job links all files and we therefore
    ## don't need to submit anything more.
    def create_submit_add_and_save(prow, proctable, check_outputs=check_for_outputs):
        log.info(f"\nProcessing: {prow}\n")
        prow = create_and_submit(prow, dry_run=dry_run_level, queue=queue,
                                 reservation=reservation,
                                 strictly_successful=True,
                                 check_for_outputs=check_outputs,
                                 resubmit_partial_complete=resubmit_partial_complete,
                                 system_name=system_name,
                                 use_specter=use_specter)
        if 'REFNIGHT' in prow:
            if isinstance(prow, Table.Row):
                prow = table_row_to_dict(prow)
            del prow['REFNIGHT']
        ## Add the processing row to the processing table
        proctable.add_row(prow)
        if len(proctable) > 0 and dry_run_level < 3:
            write_table(proctable, tablename=proc_table_pathname)
        sleep_and_report(exp_cadence_time,
                         message_suffix=f"to slow down the queue submission rate",
                         dry_run=dry_run)
        return prow, proctable

    ## Actually process the calibrations
    ## Only run if we haven't already linked or done fiberflatnight's
    if calibjobs['linkcal'] is None and calibjobs['nightlyflat'] is None:
        ptable, calibjobs, int_id = submit_calibrations(cal_etable, ptable,
                                                cal_override, calibjobs, int_id,
                                                create_submit_add_and_save)

    ## Require some minimal level of calibrations to process science exposures
    if require_cals and calibjobs['linkcal'] is None and calibjobs['nightlyflat'] is None:
        err = (f"Required to have at least psf calibrations via override link"
               + f" or psfnight")
        log.error(err)
        sys.exit(1)

    ## Process Sciences
    ## Loop over new tiles and process them
    for tile in tiles_to_proc:
        log.info(f'\n\n##################### {tile} #########################')

        ## Identify the science exposures for the given tile
        sciences = sci_etable[sci_etable['TILEID'] == tile]

        # don't submit cumulative redshifts for lasttile if it isn't in tiles_cumulative
        cur_z_submit_types = z_submit_types.copy()
        if ((z_submit_types is not None) and ('cumulative' in z_submit_types)
            and (tile not in tiles_cumulative)):
            cur_z_submit_types.remove('cumulative')

        ptable, internal_id = submit_tilenight_and_redshifts(
                                    ptable, sciences, calibjobs, int_id,
                                    dry_run=dry_run_level, queue=queue,
                                    reservation=reservation,
                                    strictly_successful=True,
                                    check_for_outputs=check_for_outputs,
                                    resubmit_partial_complete=resubmit_partial_complete,
                                    system_name=system_name,
                                    use_specter=use_specter,
                                    z_submit_types=cur_z_submit_types,
                                    laststeps=science_laststeps)

        if len(ptable) > 0 and dry_run_level < 3:
            write_table(ptable, tablename=proc_table_pathname)

        sleep_and_report(exp_cadence_time,
                         message_suffix=f"to slow down the queue submission rate",
                         dry_run=dry_run)

        ## Flush the outputs
        sys.stdout.flush()
        sys.stderr.flush()

    ################### Wrap things up ###################
    unproc_table = None
    if len(ptable) > 0:
        ## All jobs now submitted, update information from job queue and save
        ptable = update_from_queue(ptable, dry_run=dry_run_level)
        if dry_run_level < 3:
            write_table(ptable, tablename=proc_table_pathname)
            ## Now that processing is complete, lets identify what we didn't process
            processed = np.isin(full_etable['EXPID'], np.unique(np.concatenate(ptable['EXPID'])))
            unproc_table = full_etable[~processed]
            write_table(unproc_table, tablename=unproc_table_pathname)
    elif dry_run_level < 3 and len(full_etable) > 0:
        ## Done determining what not to process, so write out unproc file
        unproc_table = full_etable
        write_table(unproc_table, tablename=unproc_table_pathname)

    if dry_run_level >= 3:
        log.info(f"{dry_run_level=} so not saving outputs.")
        log.info(f"\n{full_etable=}")
        log.info(f"\nn{ptable=}")
        log.info(f"\n{unproc_table=}")

    log.info(f"Completed submission of exposures for night {night}.", '\n\n\n')
    return ptable, unproc_table




def submit_calibrations(cal_etable, ptable, cal_override, calibjobs, int_id,
                        curnight, create_submit_add_and_save):
    log = get_logger()
    processed_cal_expids = np.unique(np.concatenate(ptable['EXPIDS']).astype(int))

    ######## Submit caliblink if requested ########
    if 'refnight' in cal_override and calibjobs['linkcal'] is None:
        log.warning(f"Only using refnight from the override file for cals.")
        prow = default_prow()
        prow['INTID'] = int_id
        int_id += 1
        prow['JOBDESC'] = 'linkcal'
        prow['OBSTYPE'] = 'link'
        prow['CALIBRATOR'] = 1
        prow['NIGHT'] = curnight
        ## REFNIGHT will be removed before appending to the processing table
        prow['REFNIGHT'] = cal_override['REFNIGHT']
        prow, ptable = create_submit_add_and_save(prow, ptable,
                                                  check_outputs=False)
        calibjobs[prow['JOBDESC']] = prow.copy()

        return ptable, calibjobs, int_id
    elif len(cal_etable) == 0:
        return ptable, calibjobs, int_id

    ## Otherwise proceed with submitting the calibrations
    ## Define objects to process
    dark_erow, zeros, arcs, flats = None, None, None, None
    num_zeros, num_cte = 0, 0
    if 'zero' in cal_etable['OBSTYPE']:
        zeros = cal_etable[cal_etable['OBSTYPE']=='zero']
        num_zeros = len(zeros)
    if 'dark' in cal_etable['OBSTYPE']:
        dark_erow = cal_etable[cal_etable['OBSTYPE']=='dark'][0]
    if 'arc' in cal_etable['OBSTYPE']:
        arcs = cal_etable[cal_etable['OBSTYPE']=='arc']
    if 'flat' in cal_etable['OBSTYPE']:
        flats = cal_etable[cal_etable['OBSTYPE']=='flat']
        num_cte = np.sum(['cte' in prog.lower() for prog in flats])

    ## If just starting out and no dark, do the nightlybias
    if dark_erow is None and num_zeros > 0 and calibjobs['nightlybias'] is None:
        log.info("\nNo dark found. Submitting nightlybias before processing "
                 "exposures.\n")
        prow = erow_to_prow(zeros[0])
        prow['INTID'] = int_id
        int_id += 1
        prow['JOBDESC'] = 'nightlybias'
        prow['CALIBRATOR'] = 1
        cams = set(decode_camword('a0123456789'))
        for zero in zeros:
            if 'calib' in zero['PROGRAM']:
                proccamword = difference_camwords(zero['CAMWORD'],
                                                  zero['BADCAMWORD'])
                cams = cams.intersection(set(decode_camword(proccamword)))
        prow['PROCCAMWORD'] = create_camword(list(cams))
        prow = define_and_assign_dependency(prow, calibjobs)
        prow, ptable = create_submit_add_and_save(prow, ptable)
        calibjobs['nightlybias'] = prow.copy()
    elif dark_erow is not None:
        ######## Submit dark or ccdcalib ########
        ## process dark for bad columns even if we don't have zeros for nightlybias
        ## ccdcalib = nightlybias(zeros) + badcol(dark) + cte correction
        if num_zeros == 0 and num_cte == 0:
            jobdesc = 'badcol'
        else:
            jobdesc = 'ccdcalib'

        if calibjobs[jobdesc] is None:
            prow, int_id = make_exposure_prow(dark_erow, int_id, calibjobs, jobdesc=jobdesc)
            prow['CALIBRATOR'] = 1
            prow, ptable = create_submit_add_and_save(prow, ptable)
            calibjobs[prow['JOBDESC']] = prow.copy()

    ######## Submit arcs and psfnight ########
    if calibjobs['psfnight'] is None:
        for arc_erow in arcs:
            if arc_erow['EXPID'] in processed_cal_expids:
                continue
            prow, int_id = make_exposure_prow(arc_erow, int_id, calibjobs)
            log.info(f"\nProcessing: {prow}\n")
            prow, ptable = create_submit_add_and_save(prow, ptable)

        arc_prows = ptable[ptable['OBSTYPE']=='arc']
        joint_prow, int_id = make_joint_prow(arc_prows, descriptor='psfnight',
                                             internal_id=int_id)
        ptable = set_calibrator_flag(arc_prows, ptable)
        joint_prow, ptable = create_submit_add_and_save(joint_prow, ptable)


    ######## Submit flats and nightlyflat ########
    for flat_erow in flats:
        if flat_erow['EXPID'] in processed_cal_expids:
            continue

        if 'cte' in flat_erow['PROGRAM'] or flat_erow['EXPTIME'] < 119:
            jobdesc = 'cteflat'
        else:
            jobdesc = 'flat'
            ## If nightlyflat defined we don't need to process more normal flats
            if calibjobs['nightlyflat'] is not None:
                continue

        prow, int_id = make_exposure_prow(flat_erow, int_id, calibjobs,
                                          jobdesc=jobdesc)
        prow, ptable = create_submit_add_and_save(prow, ptable)

    if calibjobs['nightlyflat'] is None:
        flat_prows = ptable[ptable['JOBDESC'] == 'flat']
        joint_prow, int_id = make_joint_prow(flat_prows, descriptor='nightlyflat',
                                             internal_id=int_id)
        ptable = set_calibrator_flag(flat_prows, ptable)
        joint_prow, ptable = create_submit_add_and_save(joint_prow, ptable)

    return ptable, calibjobs, int_id




