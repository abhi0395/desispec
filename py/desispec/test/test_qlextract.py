from __future__ import absolute_import, division, print_function

try:
    from specter.psf import load_psf
    nospecter = False
except ImportError:
    from desiutil.log import get_logger
    log = get_logger()
    log.error('specter not installed; skipping extraction tests')
    nospecter = True

import unittest
import uuid
import os
import tempfile
import shutil
from glob import glob
from importlib import resources

import desispec.image
import desispec.io
import desispec.scripts.extract

from astropy.io import fits
import numpy as np

class TestExtract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.origdir = os.getcwd()
        cls.testdir = tempfile.mkdtemp()
        os.chdir(cls.testdir)
        cls.testhash = uuid.uuid4()
        cls.imgfile = 'test-img-{}.fits'.format(cls.testhash)
        cls.outfile = 'test-out-{}.fits'.format(cls.testhash)
        cls.outmodel = 'test-model-{}.fits'.format(cls.testhash)
        cls.fibermapfile = 'test-fibermap-{}.fits'.format(cls.testhash)
        cls.psffile = resources.files('specter').joinpath('test/t/psf-monospot.fits')
        # cls.psf = load_psf(cls.psffile)

        pix = np.random.normal(0, 3.0, size=(400,400))
        ivar = np.ones_like(pix) / 3.0**2
        mask = np.zeros(pix.shape, dtype=np.uint32)
        mask[200] = 1
        img = desispec.image.Image(pix, ivar, mask, camera='z0')
        desispec.io.write_image(cls.imgfile, img, meta=dict(flavor='science'))

        fibermap = desispec.io.empty_fibermap(100)
        desispec.io.write_fibermap(cls.fibermapfile, fibermap)

    def setUp(self):
        os.chdir(self.testdir)
        for filename in (self.outfile, self.outmodel):
            if os.path.exists(filename):
                os.remove(filename)

    @classmethod
    def tearDownClass(cls):
        #- Remove testdir only if it was created by tempfile.mkdtemp
        if cls.testdir.startswith(tempfile.gettempdir()) and os.path.exists(cls.testdir):
            shutil.rmtree(cls.testdir)

        os.chdir(cls.origdir)

    def test_boxcar(self):
        from desispec.quicklook.qlboxcar import do_boxcar
        from desispec.io import read_xytraceset
        
        #psf = load_psf(self.psffile)
        tset = read_xytraceset(self.psffile)
        pix = np.random.normal(0, 3.0, size=(tset.npix_y, tset.npix_y))
        ivar = np.ones_like(pix) / 3.0**2
        mask = np.zeros(pix.shape, dtype=np.uint32)
        img = desispec.image.Image(pix, ivar, mask, camera='z0')

        outwave = np.arange(7500, 7600)
        nwave = len(outwave)
        nspec = 5
        flux, ivar, resolution = do_boxcar(img, tset, outwave, boxwidth=2.5, nspec=nspec)

        self.assertEqual(flux.shape, (nspec, nwave))
        self.assertEqual(ivar.shape, (nspec, nwave))
        self.assertEqual(resolution.shape[0], nspec)
        # resolution.shape[1] is number of diagonals; picked by algorithm
        self.assertEqual(resolution.shape[2], nwave)
