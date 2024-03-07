# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-
"""Test desispec.scripts.proc_night
"""

import os
import glob
import unittest
import tempfile
import shutil
import importlib

import numpy as np

from desispec.workflow.tableio import load_table
from desispec.workflow.redshifts import get_ztile_script_pathname
from desispec.workflow.desi_proc_funcs import get_desi_proc_tilenight_batch_file_pathname
from desispec.io import findfile

from desispec.scripts.proc_night import proc_night

class TestProcNight(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.reduxdir = tempfile.mkdtemp()
        cls.specprod = 'test'
        cls.proddir = os.path.join(cls.reduxdir, cls.specprod)
        cls.night = 20230914

        cls.origenv = os.environ.copy()
        os.environ['DESI_SPECTRO_REDUX'] = cls.reduxdir
        os.environ['SPECPROD'] = cls.specprod

        os.makedirs(cls.proddir)
        expdir = importlib.resources.files('desispec').joinpath('test', 'data', 'exposure_tables')
        shutil.copytree(expdir, os.path.join(cls.proddir, 'exposure_tables'))

        cls.etable_file = findfile('exposure_table', cls.night)
        cls.etable = load_table(cls.etable_file)

    def tearDown(self):
        # remove everything from prod except exposure_tables
        for path in glob.glob(self.proddir+'/*'):
            if os.path.basename(path) == 'exposure_tables':
                pass
            elif os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.reduxdir)
        for key in ('DESI_SPECTRO_REDUX', 'SPECPROD'):
            if key in cls.origenv:
                os.environ[key] = cls.origenv[key]
            else:
                del os.environ[key]

    def test_proc_night(self):
        proctable, unproctable = proc_night(self.night, z_submit_types=['cumulative',],
                                            dry_run_level=1, sub_wait_time=0.0)
        
        # processing table file created
        self.assertTrue(os.path.isfile(findfile('processing_table', self.night)))

        # every tile is represented
        self.assertEqual(set(self.etable['TILEID']), set(proctable['TILEID']))

        # every step is represented
        for jobdesc in ('ccdcalib', 'arc', 'psfnight', 'flat', 'nightlyflat', 'tilenight', 'cumulative'):
            self.assertIn(jobdesc, proctable['JOBDESC'])

        # tilenight jobs created
        for tileid in np.unique(proctable['TILEID']):
            if tileid<0: continue
            batchscript = get_desi_proc_tilenight_batch_file_pathname(self.night, tileid) + '.slurm'
            self.assertTrue(os.path.exists(batchscript), f'Missing {batchscript}')

        # ztile jobs created
        ii = proctable['JOBDESC'] == 'cumulative'
        for prow in proctable[ii]:
            batchscript = get_ztile_script_pathname(tileid=prow['TILEID'], group='cumulative', night=self.night)
            self.assertTrue(os.path.exists(batchscript), f'Missing {batchscript}')

        # internal IDs are unique per row
        unique_intids = np.unique(proctable['INTID'])
        self.assertEqual(len(unique_intids), len(proctable))

    def test_proc_night_dryrun3(self):
        """Test that dry_run_level=3 doesn't produce any output"""
        proctable, unproctable = proc_night(self.night, z_submit_types=['cumulative',],
                                            dry_run_level=3, sub_wait_time=0.0)

        prodfiles = glob.glob(self.proddir+'/*')
        self.assertEqual(len(prodfiles), 1)
        self.assertTrue(prodfiles[0].endswith('exposure_tables'))



