#!/usr/bin/env python
# coding: utf-8

import os
import sys
import time
import argparse

import fitsio
import numpy as np
import pandas as pd

from desitarget.targets import main_cmx_or_sv
from desitarget.targetmask import desi_mask
from desitarget.sv3.sv3_targetmask import desi_mask as sv3_mask
from desitarget.sv2.sv2_targetmask import desi_mask as sv2_mask
from desitarget.sv1.sv1_targetmask import desi_mask as sv1_mask
from desitarget.cmx.cmx_targetmask import cmx_mask

from desiutil.log import get_logger

from desispec.io.util import get_tempfilename
from desispec.mgii_afterburner import mgii_fitter


log = get_logger()


def parse(options=None):
    parser = argparse.ArgumentParser(description="Run MgII fitter on coadd file")

    parser.add_argument("--coadd", type=str, required=True,
                        help="coadd file containing spectra")
    parser.add_argument("--redrock", type=str, required=True,
                        help="redrock file associated (in the same folder) to the coadd file")
    parser.add_argument("--output", type=str, required=True,
                        help="output filename where the result of the MgII will be saved")

    parser.add_argument("--target_selection", type=str, required=False, default="restricted",
                        help="on which sample the mgII fitter is performed: \
                              restricted (QSO targets with SPECTYPE==GALAXY) \
                              / qso_targets (QSO targets) -- qso (works also) \
                              / all_targets (All targets in the coadd file) -- all (works also)")
    parser.add_argument("--save_target", type=str, required=False, default="restricted",
                        help="which objects will be saved in the output files: \
                              restricted (objects which are identify as QSO by the mgII afterburner) \
                              / all (All objects which are tested by the mgII fitter) \
                              --> To have 500 objects in the ouput file: set --target_selection all_targets --save_target all")

    parser.add_argument("--template_dir", type=str, required=False, default=None,
                        help="give the RR templates used in mgII fitter to compare the Xi2, by default use those from redrock")

    parser.add_argument("--lambda_width", type=str, required=False, default=250,
                        help="parameter for mgII fitter, see mgii_afterburner.py for more information")
    parser.add_argument("--max_sigma", type=str, required=False, default=200,
                        help="parameter for mgII fitter, see mgii_afterburner.py for more information")
    parser.add_argument("--min_sigma", type=str, required=False, default=10,
                        help="parameter for mgII fitter, see mgii_afterburner.py for more information")
    parser.add_argument("--min_deltachi2", type=str, required=False, default=16,
                        help="parameter for mgII fitter, see mgii_afterburner.py for more information")
    parser.add_argument("--min_signifiance_A", type=str, required=False, default=3,
                        help="parameter for mgII fitter, see mgii_afterburner.py for more information")
    parser.add_argument("--min_A", type=str, required=False, default=0.0,
                        help="parameter for mgII fitter, see mgii_afterburner.py for more information")

    if options is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(options)

    return args


def select_targets_with_mgii_fitter(redrock, fibermap, sel_to_mgii, spectra_name, redrock_name, param_mgii_fitter, DESI_TARGET, save_target):
    """
    Run QuasarNet to the object with index_to_QN == True from spectra_name.
    Then, Re-Run RedRock for the targetids which are selected by QN as a QSO.
    Args:
        redrock: fitsio hdu 'REDSHIFTS' from redrock file
        fibermap:  fitsio hdu 'FIBERMAP' from redrock file
        sel_to_mgii (bool array): size 500. Select on which objects mgii will be apply (index based on redrock table)
        spectra_name / redrock_name (str): The name of the spectra / associated redrock file
        param_mgii_fitter (dict): contains info for the MgII fitter as lambda_width, max_sigma
                                  min_sigma, min_deltachi2, min_signifiance_A, min_A
        DESI_TARGET (str): name of DESI_TARGET for the wanted version of the target selection
        save_target (str) : restricted (save only IS_QSO_MGII==true targets) / all (save all the sample)
    Returns:
        QSO_sel (pandas dataframe): contains all the information useful to build the QSO cat
    """

    # just to speed up the process and don't go in mgii_fitter function for nothing..
    if sel_to_mgii.sum() == 0:
        QSO_sel = pd.DataFrame()
    else:
        (index_selected_with_mgii_fit,
         fits_result,
         index_with_mgii_fit) = mgii_fitter(spectra_name, redrock_name, sel_to_mgii,
                                            param_mgii_fitter['lambda_width'],
                                            template_dir=param_mgii_fitter['template_dir'],
                                            max_sigma=param_mgii_fitter['max_sigma'],
                                            min_sigma=param_mgii_fitter['min_sigma'],
                                            min_deltachi2=param_mgii_fitter['min_deltachi2'],
                                            min_A=param_mgii_fitter['min_A'],
                                            min_signifiance_A=param_mgii_fitter['min_signifiance_A'])

        sel_MGII = sel_to_mgii.copy()
        # we only consider index where the mgii fit was done
        sel_MGII[sel_to_mgii] = index_with_mgii_fit
        # we then conserve only index where the mgii fit gives a good result !
        sel_MGII[sel_MGII] = index_selected_with_mgii_fit

        # Build dataframe to store the result
        QSO_sel = pd.DataFrame()

        if save_target == 'restricted':
            index_to_save = sel_MGII.copy()
            index_to_save_fit_result = index_with_mgii_fit.copy()
            # keep only object selected by MGII
            index_to_save_fit_result[index_with_mgii_fit] = index_selected_with_mgii_fit
        elif save_target == 'all':
            index_to_save = sel_to_mgii.copy()
            # save every object with nan value if it is necessary --> there are sel_to_mgii.sum() objects to save
            # index_with_mgii_fit is size of sel_to_mgii.sum()
            index_to_save_fit_result = np.ones(sel_to_mgii.sum(), dtype=bool)
        else:
            # never happen since a test is performed before running this function in desi_qso_mgii_afterburner
            log.error('**** CHOOSE CORRECT SAVE_TARGET FLAG (restricted / all) ****')

        QSO_sel['TARGETID'] = redrock['TARGETID'][index_to_save]
        QSO_sel['RA'] = fibermap['TARGET_RA'][index_to_save]
        QSO_sel['DEC'] = fibermap['TARGET_DEC'][index_to_save]
        QSO_sel['Z_RR'] = redrock['Z'][index_to_save]
        QSO_sel['ZERR'] = redrock['ZERR'][index_to_save]
        QSO_sel['COEFFS'] = redrock['COEFF'][index_to_save].tolist()
        QSO_sel['SPECTYPE'] = redrock['SPECTYPE'][index_to_save]
        QSO_sel[DESI_TARGET] = fibermap[DESI_TARGET][index_to_save]
        QSO_sel['IS_QSO_MGII'] = sel_MGII[index_to_save]

        # Add info from MgII fitter output:
        # index_with_mgii_fit and index_to_save_fit_result are size of sel_to_mgii.sum()
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 0]
        QSO_sel['DELTA_CHI2'] = tmp_arr[index_to_save_fit_result]
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 1]
        QSO_sel['A'] = tmp_arr[index_to_save_fit_result]
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 2]
        QSO_sel['SIGMA'] = tmp_arr[index_to_save_fit_result]
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 3]
        QSO_sel['B'] = tmp_arr[index_to_save_fit_result]
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 4]
        QSO_sel['VAR_A'] = tmp_arr[index_to_save_fit_result]
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 5]
        QSO_sel['VAR_SIGMA'] = tmp_arr[index_to_save_fit_result]
        tmp_arr = np.nan * np.ones(sel_to_mgii.sum())
        tmp_arr[index_with_mgii_fit] = fits_result[:, 6]
        QSO_sel['VAR_B'] = tmp_arr[index_to_save_fit_result]

    return QSO_sel


def save_dataframe_to_fits(dataframe, filename, DESI_TARGET, clobber=True):
    """
    Save info from pandas dataframe in a fits file. Need to write the dtype array
    because of the list in the pandas dataframe (no other solution found)
    Args:
        dataframe (pandas dataframe): dataframe containg the all the necessary QSO info
        filename (str):  name of the fits file
        DESI_TARGET (str): name of DESI_TARGET for the wanted version of the target selection
        clobber (bool): overwrite the fits file defined by filename ?
    Returns:
        None
    """
    # Ok we cannot use dataframe.to_records() car les coeffs/c_lines sont sauvegarder sous forme de list de type objet et ne peux pas etre convertit ..
    data = np.zeros(dataframe.shape[0], dtype=[('TARGETID', 'i8'), ('RA', 'f8'), ('DEC', 'f8'), ('Z_RR', 'f8'), ('ZERR', 'f4'), ('IS_QSO_MGII', '?'),
                                               (DESI_TARGET, 'i8'), ('SPECTYPE', 'U10'),  # ('COEFFS', ('f4', 10)),
                                               ('DELTA_CHI2', 'f4'), ('A', 'f4'), ('SIGMA', 'f4'), ('B', 'f4'),
                                               ('VAR_A', 'f4'), ('VAR_SIGMA', 'f4'), ('VAR_B', 'f4')])

    data['TARGETID'] = dataframe['TARGETID']
    data['RA'] = dataframe['RA']
    data['DEC'] = dataframe['DEC']
    data['Z_RR'] = dataframe['Z_RR']
    data['ZERR'] = dataframe['ZERR']
    data['IS_QSO_MGII'] = dataframe['IS_QSO_MGII']
    data[DESI_TARGET] = dataframe[DESI_TARGET]
    data['SPECTYPE'] = dataframe['SPECTYPE']
    # data['COEFFS'] = np.array([np.array(dataframe['COEFFS'][i]) for i in range(dataframe.shape[0])])

    data['DELTA_CHI2'] = dataframe['DELTA_CHI2']
    data['A'] = dataframe['A']
    data['SIGMA'] = dataframe['SIGMA']
    data['B'] = dataframe['B']
    data['VAR_A'] = dataframe['VAR_A']
    data['VAR_SIGMA'] = dataframe['VAR_SIGMA']
    data['VAR_B'] = dataframe['VAR_B']

    # Save file in temporary file to track when timeout error appears during the writing
    tmpfile = get_tempfilename(filename)
    fits = fitsio.FITS(tmpfile, 'rw')
    fits.write(data, extname='MGII')
    log.info(f'write output in: {filename}')
    fits.close()

    # Rename temporary file to output file, overwrite existing file.
    os.rename(tmpfile, filename)
    log.info(f'rename {tmpfile} to {filename}')

    return


def main(args=None):

    if not isinstance(args, argparse.Namespace):
        args = parse(options=args)

    start = time.time()

    # Param for the MgII fitter see desispec/py/desispec/mgii_afterburner.py for additional informations
    param_mgii_fitter = {'lambda_width': args.lambda_width, 'template_dir': args.template_dir, 'max_sigma': args.max_sigma, 'min_sigma': args.min_sigma,
                         'min_deltachi2': args.min_deltachi2, 'min_signifiance_A': args.min_signifiance_A, 'min_A': args.min_A}

    if os.path.isfile(args.coadd) and os.path.isfile(args.redrock):
        # Testing if there are three cameras in the coadd file. If not create a warning file.
        if np.isin(['B_FLUX', 'R_FLUX', 'Z_FLUX'], [hdu.get_extname() for hdu in fitsio.FITS(args.coadd)]).sum() != 3:
            misscamera = os.path.splitext(args.output)[0] + '.misscamera.txt'
            with open(misscamera, "w") as miss:
                miss.write(f"At least one camera is missing from the coadd file: {args.coadd}.\n")
                miss.write("This is expected for the exposure directory.\n")
                miss.write('This is NOT expected for cumulative / healpix directory!\n')
            log.warning(f"At least one camera is missing from the coadd file; warning file {misscamera} has been written.")
        else:
            # open best fit file generated by redrock
            with fitsio.FITS(args.redrock) as redrock_file:
                redrock = redrock_file['REDSHIFTS'].read()
                fibermap = redrock_file['FIBERMAP'].read()

            # from everest REDROCK hdu and FIBERMAP hdu have the same order (the indices match)
            if np.sum(redrock['TARGETID'] == fibermap['TARGETID']) == redrock['TARGETID'].size:
                log.info("SANITY CHECK: The indices of REDROCK HDU and FIBERMAP HDU match.")
            else:
                log.error("**** The indices of REDROCK HDU AND FIBERMAP DHU do not match. This is not expected ! ****")
                return 1

            # Find which selection is used (SV1/ SV2 / SV3 / MAIN / ...)
            DESI_TARGET = main_cmx_or_sv(fibermap)[0][0]

            if DESI_TARGET == 'DESI_TARGET':
                qso_mask_bit = desi_mask.mask('QSO')
            elif DESI_TARGET == 'SV3_DESI_TARGET':
                qso_mask_bit = sv3_mask.mask('QSO')
            elif DESI_TARGET == 'SV2_DESI_TARGET':
                qso_mask_bit = sv2_mask.mask('QSO')
            elif DESI_TARGET == 'SV1_DESI_TARGET':
                qso_mask_bit = sv1_mask.mask('QSO')
            elif DESI_TARGET == 'CMX_TARGET':
                qso_mask_bit = cmx_mask.mask('MINI_SV_QSO|SV0_QSO')
            else:
                log.error("**** DESI_TARGET IS NOT CMX / SV1 / SV2 / SV3 / MAIN ****")
                return 1

            is_qso_target = fibermap[DESI_TARGET] & qso_mask_bit != 0
            sel_RR = (redrock['SPECTYPE'] == 'QSO')

            if args.target_selection == 'restricted':
                # Run MgII fitter only on QSO targets with SPECTYPE!=QSO objects to save time !
                sel_to_mgii = is_qso_target & ~sel_RR
            elif args.target_selection.lower() in ('qso', 'qso_targets'):
                # Run MgII fitter only on QSO targets
                sel_to_mgii = is_qso_target
            elif args.target_selection.lower() in ('all', 'all_targets'):
                # Run MgII fitter only on all targets available in the redrock file (500 for a petal)
                sel_to_mgii = np.ones(redrock['TARGETID'].size, dtype='bool')
            else:
                log.error("**** CHOOSE CORRECT TARGET_SELECTION FLAG (restricted / qso_targets / all_targets) ****")
                return 1

            # Check args.save_target to avoid a crash after the mgii fit
            if not (args.save_target in ['restricted', 'all']):
                log.error('**** CHOOSE CORRECT SAVE_TARGET FLAG (restricted / all) ****')
                return 1

            log.info(f"Nbr objects for mgii: {sel_to_mgii.sum()}")
            QSO_from_MGII = select_targets_with_mgii_fitter(redrock, fibermap, sel_to_mgii, args.coadd, args.redrock, param_mgii_fitter, DESI_TARGET, args.save_target)

            if QSO_from_MGII.shape[0] > 0:
                log.info(f"Number of targets saved : {QSO_from_MGII.shape[0]} -- "
                         f"Selected with mgii: {QSO_from_MGII['IS_QSO_MGII'].sum()}")
                save_dataframe_to_fits(QSO_from_MGII, args.output, DESI_TARGET)
            else:
                file = open(os.path.splitext(args.output)[0] + '.notargets.txt', "w")
                file.write("No targets were selected by MgII afterburner to be a QSO.")
                file.write(f"\nThis is done with the following parametrization : target_selection = {args.target_selection}\n")
                file.write("\nIN SOME CASE (BRIGHT TILE + target_selection=QSO), this file is expected !")
                file.close()
                log.warning(f"No objects selected to save; blanck file {os.path.splitext(args.output)[0]+'.notargets.txt'} is written")

    else:  # file for the consider Tile / Night / petal does not exist
        log.error(f"**** There is problem with files: {args.coadd} or {args.redrock} ****")
        return 1

    log.info(f"EXECUTION TIME: {time.time() - start:3.2f} s.")
    return 0
