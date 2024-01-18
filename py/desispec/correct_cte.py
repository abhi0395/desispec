"""
desispec.correct_cte
====================

Methods to fit CTE effects and remove them from images.
"""

from copy import deepcopy
import os
import numpy as np
import fitsio
from desispec.calibfinder import CalibFinder
import desispec.io
import desispec.io.util
import desispec.io.xytraceset
import desispec.io.fiberflat
from desispec.trace_shifts import compute_dx_from_cross_dispersion_profiles
import desispec.preproc
from desiutil.log import get_logger
from functools import partial
from astropy.stats import sigma_clipped_stats
from scipy.optimize import least_squares
from desispec.image_model import compute_image_model
from astropy.table import Table
import specter.psf
from desispec.qproc import qfiberflat, qsky, rowbyrowextract
import copy
from scipy.ndimage import median_filter


def apply_multiple_cte_effects(amp, locations, ctefuns):
    """Apply a series of traps to an amp.

    This function assumes that it is only being given a
    single amp that has some traps on it.  The readout
    direction is always assumed to be toward smaller numbers
    on the second axis.

    Parameters
    ----------
    amp : np.ndarray
        image of amplifier
    locations : np.ndarray [int]
        locations of traps
    ctefuns : callables
        functions that can be called to apply CTE to an amp

    Returns
    -------
    amplifier after applying traps
    """

    # amp is affected by a series of traps.
    # apply them all.
    # I don't think we want to have more than one, though!
    # assume that the read out is such that we don't have to
    # reverse the amplifier to apply in the right direction;
    # that's been done for us, as has the corresponding
    # reversing of the locations.
    locations = np.array(np.atleast_1d(locations))
    s = np.argsort(-locations)
    amp_out = amp.copy()
    # sequentially step down the serial register,
    # trap-by-trap, applying the appropriate CTE
    # function to all of the pixels affected by that trap.
    # in the picture in my head, the serial register is read
    # out in the downward direction.  All of the pixels
    # above the trap are affected.  So we start with the
    # highest trap and walk down.  Those later traps
    # see the CTE-affected image from the earlier traps.

    # for the one trap case, which is the only one we actually
    # have right now, this just applies the trap function to the
    # portion of the amplifier above the trap.
    for ii in s:
        affected = np.s_[:, locations[ii]:]
        amp_out[affected] = ctefuns[ii](amp_out[affected])
    return amp_out


def correct_amp(image, ctefun, niter=1, apply_only=False):
    """Correct an amp for CTE.

    In the limit that CTE is perturbative, one can correct for CTE
    as follows:
    for i in range(niter):
    I' = CTE(I)
    I = I - (I' - I)
    This function implements that approach.

    However, for large traps, this approach correlates the noise
    and leads to poor performance; we do not use it for DESI.

    Parameters
    ----------
    image : np.ndarray
        CTE-affected amplifier
    ctefun : callable
        function that applies CTE to an amplifier
    niter : int
        number of iterations to run
    apply_only : bool
        Do not correct, just apply the CTE function to the image.

    Returns
    -------
    CTE-corrected image
    """

    # more than one iteration makes the noise properties worse, but
    # does make things more self-consistent.
    corrected_image = image.copy()
    log = get_logger()
    for i in range(niter):
        cte_image = ctefun(corrected_image)
        if apply_only:
            return cte_image
        correction = cte_image - corrected_image
        mn, med, rms = sigma_clipped_stats(correction)
        log.info(f'Correcting CTE, iteration {i}, correction rms {rms:6.3f}')
        corrected_image = image - correction
    return corrected_image


def add_cte(img, cte_transfer_func=None, **cteparam):
    """Add CTE to a region affected by CTE.

    Assumes all of img is affected.

    Parameters
    ----------
    img : np.ndarray
        input image
    cte_transfer_func : callable
        function describing how many electrons trap soaks up and emits
    **cteparam : dictionary
        additional arguments to cte_transfer_func

    Returns
    -------
    np.ndarray
    img with additional CTE applied
    """
    if cte_transfer_func is None:
        cte_transfer_func = simplified_regnault
    out = img.copy()
    in_trap = img[:, 0] * 0.0
    for i in range(img.shape[1]):
        transfer_amount = cte_transfer_func(
            np.clip(img[:, i], 0, np.inf), in_trap, **cteparam)
        in_trap -= transfer_amount
        out[:, i] += transfer_amount
    return out


def get_amps_and_cte(header,with_params=True):
    """Get amp and CTE information from image header.

    Parameters
    ----------
    header (can be image.meta)
    with_params : bool
        if true, will return only amplifiers and
        CTE sectors with fitted parameters
        as found in the nightly calib
        if false, will return amplifiers and CTE
        sector columns for cameras with keywords
        CTECOLSX (with X the amplifier Id) in
        their calibration.

    Returns
    -------
    amp_regions, cte_regions
    amp_regions : dict
        dictionary of slices defining each amp
    cte_regions : dict
        dictionary.
        The keys are the names of the amplifier.
        Each entry is a list containing one entry for each trap.
        on that amplifier.
        Each item in the list is dictionary containing the start location,
        stop location, and a callable giving the CTE function for a trap.
    """
    # get sector info from metadata

    log = get_logger()
    cfinder = CalibFinder([header])
    amps = desispec.preproc.get_amp_ids(header)
    night = desispec.preproc.header2night(header)
    camera = header['CAMERA'].lower()

    ctecorrnight_table = None
    if with_params :
        # look for CTE param table for this camera
        filename = desispec.io.findfile('ctecorrnight', night=night, camera=camera)
        log.debug(f"Looking for file {filename}")
        if os.path.isfile(filename) :
            ctecorrnight_table = Table.read(filename)
        else :
            log.warning(f"No CTE file {filename}")

    amp_regions = dict()
    cte_regions = dict()
    for amp in amps:

        key = "CTECOLS"+amp
        if not cfinder.haskey(key) :
            # that's ok, we don't expect this keyword for each camera and amplifier luckily
            log.debug(f"No {key} for {camera} on {night}")
            continue

        if with_params :

            if ctecorrnight_table is None :
                # we do expect a CTE file because we know the effect is there and
                # we asked for the parameters, this is an error
                mess = f"Missing CTE file {filename}"
                log.error(mess)
                raise RuntimeError(mess)

            selection = (ctecorrnight_table["NIGHT"]==night)&(ctecorrnight_table["CAMERA"]==camera)&(ctecorrnight_table["AMPLIFIER"]==amp)
            if np.sum(selection)==0 :
                # we do expect a set of CTE parameter for the amplifier because we know the effect is there and
                # we asked for the parameters, this is an error
                mess = f"No CTE correction in file {filename} for amplifier {amp}"
                log.error(mess)
                raise RuntimeError(mess)

        value = cfinder.value(key)

        amp_sec = desispec.preproc.parse_sec_keyword(header["CCDSEC"+amp])

        yb = amp_sec[0].start
        ye = amp_sec[0].stop
        cte_regions_in_amp = list()
        for offcols in value.split(","):
            if len(offcols)==0 : continue
            vals  = offcols.split(":")
            nvals = len(vals)
            if nvals != 2 :
                mess = "cannot decode {}={}".format(key, value)
                log.error(mess)
                raise KeyError(mess)

            start, stop = int(vals[0]), int(vals[1])

            if with_params :
                selection2 = selection&(ctecorrnight_table["SECTOR"]==offcols)
                if np.sum(selection2)==0 :
                    log.info(f"No CTE correction in file {filename}, amplifier {amp}, sector {offcols}")
                    continue
                entry=np.where(selection2)[0][0]



            xb = max(amp_sec[1].start, start)
            xe = min(amp_sec[1].stop, stop)
            sector = [yb, ye, xb, xe]

            cteparam={"start":start,"stop":stop}
            if  with_params :
                for k in ["FUNC","AMPLITUDE","FRACLEAK"] :
                    cteparam[k]=ctecorrnight_table[k][entry]

            log.info(f"CTE correction in amplifier {amp}, sector {offcols}, {cteparam}")
            cte_regions_in_amp.append(cteparam)

        # only add if we have a model for it
        if len(cte_regions_in_amp)>0 :
            amp_regions[amp] = amp_sec
            cte_regions[amp] = cte_regions_in_amp

    return amp_regions, cte_regions


def correct_image(image, **kw):
    """Correct an image for CTE.

    This function wraps correct_amp, looping over amplifiers and reversing
    readout directions as appropriate.  The approach taken here correlates the
    noise and is not actually used for DESI.

    Parameters
    ----------
    image : desispec.io.image.Image
        the image to correct
    cteparam : dict or None
        dictionary of form {'z1C': {'543:2057': (115.0, 0.21)}} that
        provides the CTE parameters for the trap on a particular image.
    **kw : dict
        additional arguments passed to correct_amp

    Returns
    -------
    outimage : desispec.io.image.Image
    CTE-corrected image
    """
    amp, cte = get_amps_and_cte(image, cteparam)
    outimage = deepcopy(image)
    for ampname in amp:
        ampreg = amp[ampname]
        imamp = outimage[ampreg]
        cteamp = cte[ampname]
        if len(cteamp) == 0:
            # don't need to do anything in this case.
            continue
        need_to_reverse = ampreg[1].stop == image.pix.shape[1]
        if need_to_reverse:
            field, offset, sign = 'stop', ampreg[1].stop, -1
        else:
            field, offset, sign = 'start', 0, 1
        for x in cteamp:
            if x['function'] is None:
                log.info('Not correcting CTE on {ampname} at {loc}; '
                         'not included in calib information.')

        cteamp = [x for x in cteamp if x['function'] is not None]
        ctelocs = [sign * (x[field] - offset) for x in cteamp]
        individual_ctefuns = [x['function'] for x in cteamp]

        ctefun = partial(apply_multiple_cte_effects, locations=ctelocs,
                         ctefuns=individual_ctefuns)
        outimage.pix[ampreg] = correct_amp(imamp.pix[:, ::sign], ctefun, **kw)
    return outimage


def simplified_regnault(pixval, in_trap, amplitude, fracleak):
    """CTE transfer function of Regnault+.

    The model is
    transfer = in_trap * fracleak - pixval * (1 - in_trap / amplitude)
    with slight elaborations to prevent more electrons than exist from entering
    or leaving the trap.

    Parameters
    ----------
    pixval : float
        value of uncorrupted image
    in_trap : float
        number of electrons presently in trap
    amplitude : float
        amplitude of trap
    fracleak : float
        fraction of electrons that leak out of trap each transfer

    Returns
    -------
    int
    number of electrons that leak out of the trap into the pixel
    """
    maxin = np.minimum(pixval, amplitude - in_trap)
    # leak out of trap
    transfer_amount = np.clip(in_trap * fracleak, 0, in_trap)
    # leak into trap
    transfer_amount -= np.clip(pixval * (1 - in_trap / amplitude), 0, maxin)
    return transfer_amount
    # when full, always leaking ~amplitude * fracleak
    # and stealing back up to this amount.


def chi_simplified_regnault(param, cleantraces=None, ctetraces=None,
                            uncertainties=None):
    """Chi loss function for a Regnault transfer function."""
    models = [add_cte(trace, amplitude=param[0], fracleak=param[1])
              for trace in cleantraces]
    res = np.array([
        (m - c)/u for (m, c, u) in zip(models, ctetraces, uncertainties)])
    return res.reshape(-1)


def get_transfer_function(function_name) :
    """Returns a function from its name. Only 'simplified_regnault' so far."""
    if function_name == "simplified_regnault" :
        return simplified_regnault
    else :
        raise KeyError(f"No transfer function called '{function_name}'")


def fit_cte(images):
    """Fits CTE models to a list of images.

    This fits the parameters of the Regnault transfer function model
    to an image with a trap.  It works by comparing an unaffected amplifier
    to an amplifier with a trap along the boundary between the two amplifiers,
    solving for the trap parameters that make the unaffected image + the CTE
    effect a good match to the CTE-affected image.

    It will not work if both amplifiers have CTE, or if there are multiple CTE
    effects to solve for.

    It assumes that amp A is across from amp C and amp B is across from amp D.

    It would likely fail badly for a two-amp mode image.

    Parameters
    ----------
    images : list
        A list of images.
        Usually these are all flats with different exposure lengths on the
        same device.

    Returns
    -------
    astropy.Table with columns "NIGHT","CAMERA","AMPLIFIER","SECTOR","FUNC",
                               "AMPLITUDE","FRACLEAK","CHI2PDF"

      "NIGHT","CAMERA","AMPLIFIER" are properties of the image
      "SECTOR" is a string of the form 'BEGIN:END' defining a range of CCD cols.
      "FUNC" is a transfer function, only 'simplified_regnault' implemented for now.
      "AMPLITUDE","FRACLEAK" are parameters of the transfer function
      "CHI2PDF" is the reduced chi2 of the fit
    """
    # take a bunch of preproc images on one camera
    # these should all be flats and the same device
    # compare areas above and below the amp boundary
    # only works if only one of the two amps has a CTE effect!
    # if both have CTE effects then we need another approach.
    # assume that A <-> C and B <-> D are paired for these purposes.
    # take an uncontaminated line from one of the pair.  Apply the CTE
    # model with some parameters.  Get the contaminated line.
    # compare with the actual contaminated line.  Minimize.
    # we need to extract only the relevant region and have
    # some robustness / noise tracking.

    log = get_logger()
    log.debug("begin fit_cte")

    keys = ["NIGHT","CAMERA","AMPLIFIER","SECTOR","FUNC","AMPLITUDE","FRACLEAK","CHI2PDF"]


    if images is None :
        # nothing to do
        # return empty table with just the column names
        table = Table()
        for k in keys :
            table[k] = np.array([])
        return table

    assert len(images) > 0
    night = desispec.preproc.header2night(images[0].meta)
    camera = images[0].meta['CAMERA']
    obstype = images[0].meta['OBSTYPE']

    if obstype != 'FLAT':
        log.warning('Really should not use this function with a non-flat?!')
    # only use with flats; otherwise the matching above and below the
    # boundary is particularly fraught.
    for image in images:
        assert image.meta['CAMERA'] == camera
        assert image.meta['OBSTYPE'] == obstype
    matching_amps = {
        'A': 'C',
        'C': 'A',
        'B': 'D',
        'D': 'B',
    }
    amp, cte = get_amps_and_cte(images[0].meta, with_params=False)


    res = dict()
    for k in keys :
        res[k]=list()


    ctefits = dict()
    for ampname in amp:
        tcte = cte[ampname]
        if len(tcte) == 0:
            continue
        if len(tcte) > 1:
            raise ValueError('Two CTE effect fitting not yet implemented.')
        if matching_amps[ampname] in cte and len(cte[matching_amps[ampname]]) != 0:
            raise ValueError('CTE effect on amp and its mirror not yet '
                             'implemented.')
        tcte = tcte[0]
        # okay!  we should be able to do the fit.
        # we need to select out the region near the amp boundary.
        ampreg = amp[ampname]
        on_bottom = ampreg[0].start == 0
        if on_bottom:
            ampbd = ampreg[0].stop
        else:
            ampbd = ampreg[0].start
        npix = 11
        need_to_reverse = ampreg[1].stop == image.pix.shape[1]
        start, stop = tcte['start'], tcte['stop']

        step = 1
        if need_to_reverse:
            step = -1
            start, stop = stop, start
        scte = np.s_[ampbd:ampbd+npix, start:stop:step]
        sclean = np.s_[ampbd-npix:ampbd, start:stop:step]

        if on_bottom:
            scte, sclean = sclean, scte
        cleantraces = [np.median(im.pix[sclean], axis=0, keepdims=True)
                       for im in images]
        ctetraces = [np.median(im.pix[scte], axis=0, keepdims=True)
                     for im in images]
        # variance in median of a normal distribution is sigma^2 * pi / 2 / n
        # this is more careful than it makes sense to be here, but I
        # wanted to look it up again and figured I might as well.
        fac = np.sqrt(np.pi / 2 / npix)
        uncertainties = [
            fac * np.sqrt(np.median(
                im.ivar[sclean]**-1 + im.ivar[scte]**-1, axis=0,
                keepdims=True))
            for im in images]

        chi = partial(chi_simplified_regnault,
                      cleantraces=cleantraces,
                      ctetraces=ctetraces,
                      uncertainties=uncertainties)
        startguesses = [1, 20, 50, 100]
        chiguesses = np.array([chi([g, 0.2]) for g in startguesses])
        bestguess = np.argmin(np.sum(chiguesses**2, axis=1))
        par = least_squares(chi, [startguesses[bestguess], 0.2],
                            diff_step=[0.2, 0.01], loss='huber')
        ctefits[ampname] = dict()
        offcols = f'{tcte["start"]}:{tcte["stop"]}'
        chi2dof = par.cost / len(par.fun)
        ctefits[ampname][offcols] = (par.x, chi2dof)
        log.info(f'CTE fit chi^2 / dof = {chi2dof:5.2f}')

        res["NIGHT"].append(night)
        res["CAMERA"].append(camera)
        res["AMPLIFIER"].append(ampname)
        res["SECTOR"].append(offcols)
        res["FUNC"].append("simplified_regnault")
        res["AMPLITUDE"].append(par.x[0])
        res["FRACLEAK"].append(par.x[1])
        res["CHI2PDF"].append(chi2dof)

    table = Table()
    for k in res.keys() :
        table[k] = np.array(res[k])
    return table


def get_cte_images(night, camera):
    """Get the images needed for a CTE fit for a particular night.

    This function looks up the appropriate exposure tables to find
    the CTE-detection image and the previous image, which is
    usually a normal flat field image.

    Parameters
    ----------
    night : int
        the night YEARMMDD integer
    camera : str
        the camera, e.g., z1

    Returns
    -------
    Fit results; see fit_cte for details.
    """

    log = get_logger()

    exptablefn = os.path.join(os.environ['DESI_SPECTRO_REDUX'],
                              os.environ['SPECPROD'],
                              'exposure_tables', str(night // 100),
                              f'exposure_table_{night}.csv')

    if not os.path.isfile(exptablefn) :
        mess = f"Cannot find exposure table file '{exptablefn}'. Because of that the flat exposures needed for the CTE correction modeling cannot be identified. Maybe check env. variables DESI_SPECTRO_REDUX and SPECPROD?"
        log.error(mess)
        raise RuntimeError(mess)

    exptable = Table.read(exptablefn)

    selection = (np.abs(exptable['EXPTIME'] - 1) < 0.1) & (exptable['OBSTYPE'] == 'flat')
    if np.sum(selection)<1 :
        mess = f"No flat exposure of approx. 1s found for night {night} (in {exptablefn}). It's a requirement for the CTE correction model fit"
        log.error(mess)
        raise RuntimeError(mess)
    index1 = np.where(selection)[0][0]

    # first use the calibration finder to see if there is any CTE issue with this camera
    # so that we don't preprocess exposures for nothing

    # get header and primary header of image
    filename  = desispec.io.findfile('raw',night,exptable['EXPID'][index1])
    header    = fitsio.read_header(filename, camera.upper())
    amp, cte = get_amps_and_cte(header,with_params=False)
    if len(cte)==0 :
        log.info(f"No CTE correction to compute for {night} {camera}")
        return None

    selection = (np.abs(exptable['EXPTIME'] - 120) < 10) & (exptable['OBSTYPE'] == 'flat')
    if np.sum(selection)<1 :
        mess = f"No flat exposure of approx. 120s found for night {night} (in {exptablefn}). It's a requirement for the CTE correction model fit"
        log.error(mess)
        raise RuntimeError(mess)
    index2 = np.where(selection)[0][-1]
    exposure_indices = [index1, index2]
    log.info(f"Will use exposures {exposure_indices}")
    images = list()
    for i in exposure_indices :
        expid=exptable['EXPID'][i]
        preproc_filename = desispec.io.findfile('preproc_for_cte', night, expid, camera)
        if not os.path.isfile(preproc_filename) :
            log.info(f"Computing {preproc_filename}")
            infile = desispec.io.findfile('raw',night,expid)
            image  = desispec.io.read_raw(infile, camera, no_cte_corr = True)
            tmpfile = desispec.io.util.get_tempfilename(preproc_filename)
            desispec.io.write_image(tmpfile,image)
            os.rename(tmpfile, preproc_filename)
            log.info(f"Wrote {preproc_filename}")
            images.append(image)
        else :
            images.append(desispec.io.read_image(preproc_filename))

    return images


def fit_cte_night(night, camera):
    """Fit the CTE parameters for a particular night.

    Parameters
    ----------
    night : int
        the night YEARMMDD integer
    camera : str
        the camera, e.g., z1

    Returns
    -------
    Fit results; see fit_cte for details.
    """
    images = get_cte_images(night, camera)
    return fit_cte(images)


def get_image_model(preproc, psf=None):
    """Compute model for an image using aperture extraction.

    This computes a simple model for an image based on an aperture extraction.

    Parameters
    ----------
    preproc : Image
        Image to model

    Returns
    -------
    np.ndarray
    Model image
    """
    meta = preproc.meta
    cfinder = CalibFinder([meta])
    psf_filename = cfinder.findfile("PSF")
    xyset = desispec.io.xytraceset.read_xytraceset(psf_filename)
    fiberflat_filename = cfinder.findfile("FIBERFLAT")
    fiberflat = desispec.io.fiberflat.read_fiberflat(fiberflat_filename)
    with_sky_model = True
    with_spectral_smoothing = True
    spectral_smoothing_sigma_length = 71
    no_traceshift = False

    mask = preproc.mask
    mimage = compute_image_model(
        preproc, xyset, fiberflat=fiberflat,
        with_spectral_smoothing=with_spectral_smoothing,
        spectral_smoothing_sigma_length=spectral_smoothing_sigma_length,
        with_sky_model=with_sky_model,
        psf=psf,
        fit_x_shift=(not no_traceshift))
    preproc.mask = mask
    # compute_image_model sets this to None for some reason.
    # we're restoring it.
    return mimage


def get_rowbyrow_image_model(preproc, fibermap=None,
                             spectral_smoothing_sigma_length=31,
                             nspec=500, psf=None):
    """Compute row-by-row image model.

    This model uses a simultaneous PSF fit in each row to get better
    performance than get_image_model at the expense of reduced speed.
    The extracted fluxes are then combined with the PSF to produce a
    2D model image.

    Parameters
    ----------
    preproc : Image
        image to model
    fibermap : astropy.Table
        fibermap to use with image
    spectral_smoothing_sigma_length : int
        amount to smooth source spectra in model
    nspec : int
        number of spectra to extract and model
    psf : specter.psf.gausshermite.GaussHermitePSF
        PSF to use

    Returns
    -------
    np.ndarray
    Model image.
    """
    meta = preproc.meta
    cfinder = CalibFinder([meta])
    if fibermap is None and hasattr(preproc, 'fibermap'):
        fibermap = preproc.fibermap
    if psf is None:
        psf_filename = cfinder.findfile("PSF")
        psf = specter.psf.load_psf(psf_filename)
        # try to update the trace shifts first?
        xytraceset = desispec.io.xytraceset.read_xytraceset(psf_filename)
        x, y, dx, ex, fiber, wave = compute_dx_from_cross_dispersion_profiles(
            xcoef=xytraceset.x_vs_wave_traceset._coeff,
            ycoef=xytraceset.y_vs_wave_traceset._coeff,
            wavemin=xytraceset.wavemin,
            wavemax=xytraceset.wavemax,
            image=preproc,
            fibers=np.arange(xytraceset.nspec, dtype=int))
        dx = np.median(dx)
        psf._x._coeff[:, 0] += dx

    res = rowbyrowextract.extract(preproc, psf, nspec=nspec,
                                  fibermap=fibermap, return_model=True)
    qframe, model, profile, profilepix = res

    fiberflat_filename = cfinder.findfile("FIBERFLAT")
    fiberflat = desispec.io.fiberflat.read_fiberflat(fiberflat_filename)
    fqframe = copy.deepcopy(qframe)
    flat = qfiberflat.qproc_apply_fiberflat(
        fqframe, fiberflat, return_flat=True)
    sfqframe = copy.deepcopy(fqframe)
    sky = qsky.qproc_sky_subtraction(sfqframe, return_skymodel=True)
    sfflux = median_filter(
        sfqframe.flux, size=(1, spectral_smoothing_sigma_length),
        mode='nearest')
    modflux = (sfflux + sky) * flat
    mframe = copy.deepcopy(sfqframe)
    mframe.flux = modflux
    return rowbyrowextract.model(mframe, profile, profilepix,
                                 preproc.pix.shape)


def correct_image_via_model(image, niter=5):
    """Correct for CTE via an image model.

    The idea here is that you can roughly extract spectra from a
    CTE-affected image just by extracting as usual.  You can then
    create a noise-free image from that extraction in the context
    of a PSF and traces.  You can then apply CTE to that noise-free
    image.  The difference between the CTE-affected image and the
    original image is a noise-free version of what CTE is doing to your
    data, which you can then subtract.

    You can then re-extract from the corrected image, and repeat, improving
    the CTE correction.

    As pseudocode, this corresponds to:
    for i in range(niter):
    M = get_model(I)
    M' = CTE(M)
    I = I - (M' - M)
    This function implements that approach.

    Parameters
    ----------
    image : Image
        input image
    niter : int
        number of iterations to run

    Returns
    -------
    outimage : Image
        image after correction for CTE
    """


    log = get_logger()

    # here we get the list of amplifiers and the list
    # of sectors per amplifiers that are affected by CTE issues
    # and for which we have a model to apply
    # (only amplifers and sectors with a model are in this list)
    amp, cte = get_amps_and_cte(image.meta)
    if len(cte) == 0 :
        log.info("No CTE correction to do for this image, return original")
        return image

    outimage = deepcopy(image)

    previous_rms = 0.
    for i in range(niter):
        outmodel = get_rowbyrow_image_model(outimage)
        cteimage = outmodel.copy()

        for ampname in amp:
            ampreg = amp[ampname]
            imamp = outmodel[ampreg]
            cteamp = cte[ampname]
            if len(cteamp) == 0:
                # don't need to do anything in this case.
                continue

            need_to_reverse = ampreg[1].stop == image.pix.shape[1]
            if need_to_reverse:
                field, offset, sign = 'stop', ampreg[1].stop, -1
            else:
                field, offset, sign = 'start', 0, 1

            ctelocs = [sign * (x[field] - offset) for x in cteamp]
            individual_ctefuns = []
            for entry in cteamp :
                individual_ctefuns.append(partial( add_cte,
                                                   cte_transfer_func=get_transfer_function(entry['FUNC']),
                                                   amplitude=entry['AMPLITUDE'],
                                                   fracleak=entry['FRACLEAK']))

            cteimage[ampreg] = apply_multiple_cte_effects(
                imamp[:, ::sign], locations=ctelocs,
                ctefuns=individual_ctefuns)
            correction_amp = imamp[:, ::sign] - cteimage[ampreg]
            mn, med, rms = sigma_clipped_stats(correction_amp)
            log.info(
                f'Correcting CTE, iteration {i}, correction rms {rms:6.3f}')
            if abs(rms-previous_rms)<0.1 :
                break
            previous_rms =rms
        correction = cteimage - outmodel
        outimage.pix = image.pix - correction
    return outimage
