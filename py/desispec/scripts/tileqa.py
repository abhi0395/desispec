#!/usr/bin/env python
#
# See top-level LICENSE.rst file for Copyright information
#
# -*- coding: utf-8 -*-

"""
This script computes QA scores per exposure, after the cframe are done
"""

#- enforce a batch-friendly matplotlib backend
from desispec.util import set_backend
set_backend()

import os,sys
import argparse
import glob
import numpy as np
import multiprocessing
from astropy.table import Table
import fitsio

from desiutil.log import get_logger
from desispec.io import specprod_root,findfile,read_tile_qa,write_tile_qa
from desispec.io.util import get_tempfilename
from desispec.tile_qa import compute_tile_qa
from desispec.util import parse_int_args

from desispec.tile_qa_plot import make_tile_qa_plot


def parse(options=None):
    parser = argparse.ArgumentParser(
                description="Calculate tile QA")
    parser.add_argument('-o','--outfile', type=str, default=None, required=False,
                        help = 'Output summary file (optional)')
    parser.add_argument('--recompute', action = 'store_true',
                        help = 'recompute')
    parser.add_argument('-g', '--group', type=str, default='cumulative', required=False,
            help = "tile group: pernight or cumulative")
    parser.add_argument('--prod', type = str, default = None, required=False,
                        help = 'Path to input reduction, e.g. /global/cfs/cdirs/desi/spectro/redux/blanc/,  or simply prod version, like blanc, but requires env. variable DESI_SPECTRO_REDUX. Default is $DESI_SPECTRO_REDUX/$SPECPROD.')
    parser.add_argument('--exposure-qa-dir', type = str, default = None, required=False,
                        help = 'Path to exposure qa directory, default is the input prod directory')
    parser.add_argument('--outdir', type = str, default = None, required=False,
                        help = 'Path to ouput directory, default is the input prod directory. Files written in {outdir}/tiles/{group}/{TILEID}/{NIGHT}/')
    parser.add_argument('-t','--tileids', type = str, default = None, required=False,
                        help = 'Comma, or colon separated list of nights to process. ex: 12,14 or 12:23')
    parser.add_argument('-n','--nights', type = str, default = None, required=False,
                        help = 'Comma, or colon separated list of nights to process. ex: 20210501,20210502 or 20210501:20210531')
    parser.add_argument('--nproc', type = int, default = 1,
                        help = 'Multiprocessing.')

    args = None
    if options is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(options)
    return args

def func(night,tileid,specprod_dir,exposure_qa_dir,outfile=None, group='cumulative') :
    """
    Wrapper function to compute_tile_qa for multiprocessing
    """
    log = get_logger()
    fiberqa_table , petalqa_table = compute_tile_qa(night,tileid,specprod_dir,exposure_qa_dir,group=group)
    if fiberqa_table is None :
        return None

    write_tile_qa(outfile,fiberqa_table,petalqa_table)
    log.info("wrote {}".format(outfile))
    _wrap_make_tile_qa_plot(outfile, specprod_dir)

    if "EXTNAME" in fiberqa_table.meta :
        fiberqa_table.meta.pop("EXTNAME")

    return(fiberqa_table.meta)

def _func(arg) :
    """
    Wrapper function to compute_tile_qa for multiprocessing
    """
    return func(**arg)

def _wrap_make_tile_qa_plot(qafitsfile, specprod_dir=None):
    """
    Utility wrapper to make qa plot with try/except/log wrappers

    Args:
        qafitsfile (str): full path to tile-qa-*.fits data

    Options:
        specprod_dir (str): full path to production directory

    Returns: full path to qapngfile, or None upon failure

    Notes:
        if plotting fails, this prints traceback and logs error but doesn't
        raise and exception itself, returning None instead
    """
    if specprod_dir is None:
        specprod_dir = specprod_root() # $DESI_SPECTRO_REDUX/$SPECPROD

    log = get_logger()
    #try:
    #    figfile = make_tile_qa_plot(qafitsfile, specprod_dir)
    #except Exception as err:
    #    figfile = None
    #    import traceback
    #    lines = traceback.format_exception(*sys.exc_info())
    #    log.error("make_tile_qa_plot raised an exception:")
    #    print("".join(lines))
    figfile = make_tile_qa_plot(qafitsfile, specprod_dir)
    
    if figfile is not None :
        log.info("wrote QA plot {}".format(figfile))
    else :
        log.error("failed to compute QA plot for {}".format(qafitsfile))

    return figfile


def main(args=None):

    log = get_logger()

    if not isinstance(args, argparse.Namespace):
        args = parse(options=args)

    if args.prod is None:
        args.prod = specprod_root()
    elif args.prod.find("/")<0 :
        args.prod = specprod_root(args.prod)
    if args.outdir is None :
        args.outdir = args.prod
    if args.exposure_qa_dir is None :
        args.exposure_qa_dir = args.prod


    log.info('prod    = {}'.format(args.prod))
    log.info('outfile = {}'.format(args.outfile))

    if args.tileids is not None:
        tileids = parse_int_args(args.tileids)
    else:
        tileids = None

    dirnames = sorted(glob.glob('{}/exposures/????????'.format(args.prod)))
    nights=[]
    for dirname in dirnames :
        try :
            night=int(os.path.basename(dirname))
            nights.append(night)
        except ValueError as e :
            log.warning("ignore {}".format(dirname))

    if args.nights :
        requested_nights = parse_int_args(args.nights)
        nights=np.intersect1d(nights,requested_nights)

    log.info("nights = {}".format(nights))
    if tileids is not None : log.info('tileids = {}'.format(tileids))

    summary_rows  = list()
    night_tileids=dict()  # dict[night] = list(tiles...)
    for count,night in enumerate(nights) :
        dirnames = sorted(glob.glob('{}/tiles/{}/*/{}'.format(args.prod, args.group, night)))
        night_tileids[night] = list()
        for dirname in dirnames :
            try :
                tileid=int(os.path.basename(os.path.dirname(dirname)))
                night_tileids[night].append(tileid)
            except ValueError as e :
                log.warning("ignore {}".format(dirname))
        if tileids is not None :
            night_tileids[night] = np.intersect1d(tileids,night_tileids[night])
            if len(night_tileids[night]) == 0 :
                log.warning(f'No tiles on night {night}; continuing')
                continue

        log.info("{} {}".format(night,night_tileids[night]))

        func_args = []
        for tileid in night_tileids[night] :
            filename = findfile("tileqa",night=night,tile=tileid,specprod_dir=args.outdir, groupname=args.group)
            if not args.recompute :
                if os.path.isfile(filename) :
                    log.info("skip existing {}".format(filename))
                    head = fitsio.read_header(filename,"FIBERQA")
                    entry=dict()
                    for r in head.records() :
                        k=r['name']
                        if k in ['SIMPLE','XTENSION','BITPIX','NAXIS','NAXIS1','NAXIS2','EXTEND','PCOUNT','GCOUNT','TFIELDS','EXTNAME', 'CHECKSUM', 'DATASUM'] : continue
                        if k.find('TTYPE')>=0 or k.find('TFORM')>=0 : continue
                        entry[k]=r['value']
                    summary_rows.append(entry)
                    continue
            func_args.append({'night':night,'tileid':tileid,'specprod_dir':args.prod,'exposure_qa_dir':args.exposure_qa_dir,'outfile':filename, 'group':args.group})

        if args.nproc == 1 :
            for func_arg in func_args :
                entry = func(**func_arg)
                if entry is not None :
                    summary_rows.append(entry)
        else :
            log.info("Multiprocessing with {} procs".format(args.nproc))
            pool = multiprocessing.Pool(args.nproc)
            results  =  pool.map(_func, func_args)
            for entry in results :
                if entry is not None :
                    summary_rows.append(entry)
            pool.close()
            pool.join()

    # check for missing png files and try again
    # could be missing due to skipped fits file, or sky cutout server glitch
    for night, tileids in night_tileids.items():
        for tileid in tileids:
            qafitsfile = findfile("tileqa", night=night, tile=tileid,
                    specprod_dir=args.outdir, groupname=args.group)
            qapngfile = findfile("tileqapng", night=night, tile=tileid,
                    specprod_dir=args.outdir, groupname=args.group)

            if os.path.exists(qafitsfile) and not os.path.exists(qapngfile):
                log.info(f'Trying again on '+os.path.basename(qapngfile))
                _wrap_make_tile_qa_plot(qafitsfile, specprod_dir=args.prod)

    if args.outfile is not None and len(summary_rows)>0 :
        colnames=None
        refrow=None

        for i,row in enumerate(summary_rows) :
            keys=list(row.keys())
            if len(keys)>0 :
                if colnames is not None :
                    if len(keys) > len(colnames) :
                        colnames=keys
                        refrow=row
                else :
                    colnames=keys
                    refrow=row
        good_rows=[]
        for i,row in enumerate(summary_rows) :
            keys=list(row.keys())
            if len(keys)>0 :
                if len(keys)<len(colnames) :
                    for k in colnames :
                        if k in keys: continue
                        row[k]=refrow[k]
                        try :
                            row[k]*=0
                        except :
                            row[k]=""
                        log.warning("missing {} in {}".format(k,row["TILEID"]))
                good_rows.append(row)
            else :
                print("empty row",i)
        table = Table(rows=good_rows, names=colnames)

        print()
        print(table)
        print()

        tmpfile = get_tempfilename(args.outfile)
        table.write(tmpfile, overwrite=True, format='fits')
        os.rename(tmpfile, args.outfile)
        log.info("wrote {}".format(args.outfile))

    if len(summary_rows)==0 :
        print("no data")

    return 0
