import warnings
import numpy as np
import numpy.testing as npt
from numpy.testing import (assert_, assert_equal, assert_almost_equal,
                           assert_array_almost_equal, run_module_suite,
                           assert_array_equal)
from dipy.testing import assert_greater, assert_greater_equal
from dipy.data import get_sphere, get_fnames, default_sphere, small_sphere
from dipy.sims.voxel import (multi_tensor,
                             single_tensor,
                             multi_tensor_odf,
                             all_tensor_evecs, single_tensor_odf)
from dipy.core.gradients import gradient_table
from dipy.reconst.csdeconv import (ConstrainedSphericalDeconvModel,
                                   ConstrainedSDTModel,
                                   forward_sdeconv_mat,
                                   odf_deconv,
                                   odf_sh_to_sharp,
                                   mask_for_response_ssst,
                                   response_from_mask_ssst,
                                   auto_response_ssst,
                                   recursive_response)
from dipy.direction.peaks import peak_directions
from dipy.core.sphere import HemiSphere
from dipy.core.sphere_stats import angular_similarity
from dipy.reconst.dti import TensorModel, fractional_anisotropy
from dipy.reconst.shm import (QballModel, sf_to_sh, sh_to_sf,
                              real_sym_sh_basis, sph_harm_ind_list)
from dipy.reconst.shm import lazy_index
import dipy.reconst.dti as dti
from dipy.core.sphere import Sphere
from dipy.io.gradients import read_bvals_bvecs
from dipy.io.image import load_nifti_data


def test_recursive_response_calibration():
    """
    Test the recursive response calibration method.
    """
    SNR = 100
    S0 = 1

    _, fbvals, fbvecs = get_fnames('small_64D')

    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    sphere = default_sphere

    gtab = gradient_table(bvals, bvecs)
    evals = np.array([0.0015, 0.0003, 0.0003])
    evecs = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]]).T
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))
    angles = [(0, 0), (90, 0)]

    where_dwi = lazy_index(~gtab.b0s_mask)

    S_cross, _ = multi_tensor(gtab, mevals, S0, angles=angles,
                              fractions=[50, 50], snr=SNR)

    S_single = single_tensor(gtab, S0, evals, evecs, snr=SNR)

    data = np.concatenate((np.tile(S_cross, (8, 1)),
                           np.tile(S_single, (2, 1))),
                          axis=0)

    odf_gt_cross = multi_tensor_odf(sphere.vertices, mevals, angles, [50, 50])

    odf_gt_single = single_tensor_odf(sphere.vertices, evals, evecs)

    response = recursive_response(gtab, data, mask=None, sh_order=8,
                                  peak_thr=0.01, init_fa=0.05,
                                  init_trace=0.0021, iter=8, convergence=0.001,
                                  parallel=False)

    csd = ConstrainedSphericalDeconvModel(gtab, response)

    csd_fit = csd.fit(data)

    assert_equal(np.all(csd_fit.shm_coeff[:, 0] >= 0), True)

    fodf = csd_fit.odf(sphere)

    directions_gt_single, _, _ = peak_directions(odf_gt_single, sphere)
    directions_gt_cross, _, _ = peak_directions(odf_gt_cross, sphere)
    directions_single, _, _ = peak_directions(fodf[8, :], sphere)
    directions_cross, _, _ = peak_directions(fodf[0, :], sphere)

    ang_sim = angular_similarity(directions_cross, directions_gt_cross)
    assert_equal(ang_sim > 1.9, True)
    assert_equal(directions_cross.shape[0], 2)
    assert_equal(directions_gt_cross.shape[0], 2)

    ang_sim = angular_similarity(directions_single, directions_gt_single)
    assert_equal(ang_sim > 0.9, True)
    assert_equal(directions_single.shape[0], 1)
    assert_equal(directions_gt_single.shape[0], 1)

    with warnings.catch_warnings(record=True) as w:
        sphere = Sphere(xyz=gtab.gradients[where_dwi])
        npt.assert_equal(len(w), 1)
        npt.assert_(issubclass(w[0].category, UserWarning))
        npt.assert_("Vertices are not on the unit sphere" in str(w[0].message))
    sf = response.on_sphere(sphere)
    S = np.concatenate(([response.S0], sf))

    tenmodel = dti.TensorModel(gtab, min_signal=0.001)

    tenfit = tenmodel.fit(S)
    FA = fractional_anisotropy(tenfit.evals)
    FA_gt = fractional_anisotropy(evals)
    assert_almost_equal(FA, FA_gt, 1)


def test_mask_for_response_ssst():
    fdata, fbvals, fbvecs, ffa, fmask = get_fnames('???')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    data = load_nifti_data(fdata)
    fa = load_nifti_data(ffa)
    mask = load_nifti_data(fmask)

    gtab = gradient_table(bvals, bvecs)

    mask_w_no_fa = mask_for_response_ssst(gtab, data,
                                  roi_center=None,
                                  roi_radius=10,
                                  fa_data=None,
                                  fa_thr=0.7)

    # Verifies that mask is not empty:
    assert_equal(int(np.sum(mask_w_no_fa)) == 0, True)

    mask_w_fa = mask_for_response_ssst(gtab, data,
                            roi_center=None,
                            roi_radius=3,
                            fa_data=fa,
                            fa_thr=0.7)

    assert_array_almost_equal(mask_w_no_fa, mask_w_fa)
    assert_array_almost_equal(mask, mask_w_fa)


def test_response_from_mask_ssst():
    fdata, fbvals, fbvecs, fmask, fresponse = get_fnames('???')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    data = load_nifti_data(fdata)
    mask = load_nifti_data(fmask)
    response_gt = np.loadtxt(fresponse).T

    gtab = gradient_table(bvals, bvecs)

    response, ratio = response_from_mask_ssst(gtab, data, mask)

    assert_array_almost_equal(response, response_gt)


def test_auto_response_ssst():
    fdata, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    data = load_nifti_data(fdata)

    gtab = gradient_table(bvals, bvecs)

    response_auto, ratio_auto = auto_response_ssst(gtab,
                        data,
                        roi_center=None,
                        roi_radius=3,
                        fa_data=None,
                        fa_thr=0.7)

    mask = mask_for_response_ssst(gtab, data,
                            roi_center=None,
                            roi_radius=3,
                            fa_data=None,
                            fa_thr=0.7)
    
    response_from_mask, ratio_from_mask = response_from_mask_ssst(gtab,
                                                                   data,
                                                                   mask)
    
    assert_array_equal(response_auto, response_from_mask)
    assert_array_equal(ratio_auto, ratio_from_mask)


def test_csdeconv():
    SNR = 100
    S0 = 1

    _, fbvals, fbvecs = get_fnames('small_64D')

    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs, b0_threshold=0)
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))

    angles = [(0, 0), (60, 0)]

    S, sticks = multi_tensor(gtab, mevals, S0, angles=angles,
                             fractions=[50, 50], snr=SNR)

    sphere = get_sphere('symmetric362')
    odf_gt = multi_tensor_odf(sphere.vertices, mevals, angles, [50, 50])
    response = (np.array([0.0015, 0.0003, 0.0003]), S0)
    csd = ConstrainedSphericalDeconvModel(gtab, response)
    csd_fit = csd.fit(S)
    assert_equal(csd_fit.shm_coeff[0] > 0, True)
    fodf = csd_fit.odf(sphere)

    directions, _, _ = peak_directions(odf_gt, sphere)
    directions2, _, _ = peak_directions(fodf, sphere)

    ang_sim = angular_similarity(directions, directions2)

    assert_equal(ang_sim > 1.9, True)
    assert_equal(directions.shape[0], 2)
    assert_equal(directions2.shape[0], 2)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", category=UserWarning)
        _ = ConstrainedSphericalDeconvModel(gtab, response, sh_order=10)
        assert_greater(len([lw for lw in w if issubclass(lw.category,
                                                         UserWarning)]), 0)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", category=UserWarning)
        ConstrainedSphericalDeconvModel(gtab, response, sh_order=8)
        assert_equal(len([lw for lw in w if issubclass(lw.category,
                                                       UserWarning)]), 0)

    mevecs = []
    for s in sticks:
        mevecs += [all_tensor_evecs(s).T]

    S2 = single_tensor(gtab, 100, mevals[0], mevecs[0], snr=None)
    big_S = np.zeros((10, 10, 10, len(S2)))
    big_S[:] = S2

    aresponse, aratio = auto_response_ssst(gtab, big_S, roi_center=(5, 5, 4),
                                      roi_radius=3, fa_thr=0.5)
    assert_array_almost_equal(aresponse[0], response[0])
    assert_almost_equal(aresponse[1], 100)
    assert_almost_equal(aratio, response[0][1] / response[0][0])

    auto_response_ssst(gtab, big_S, roi_radius=3, fa_thr=0.5)
    assert_array_almost_equal(aresponse[0], response[0])


def test_odfdeconv():
    SNR = 100
    S0 = 1

    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))

    angles = [(0, 0), (90, 0)]
    S, _ = multi_tensor(gtab, mevals, S0, angles=angles,
                        fractions=[50, 50], snr=SNR)

    sphere = get_sphere('symmetric362')

    odf_gt = multi_tensor_odf(sphere.vertices, mevals, angles, [50, 50])

    e1 = 15.0
    e2 = 3.0
    ratio = e2 / e1

    csd = ConstrainedSDTModel(gtab, ratio, None)

    csd_fit = csd.fit(S)
    fodf = csd_fit.odf(sphere)

    directions, _, _ = peak_directions(odf_gt, sphere)
    directions2, _, _ = peak_directions(fodf, sphere)

    ang_sim = angular_similarity(directions, directions2)

    assert_equal(ang_sim > 1.9, True)

    assert_equal(directions.shape[0], 2)
    assert_equal(directions2.shape[0], 2)

    with warnings.catch_warnings(record=True) as w:

        ConstrainedSDTModel(gtab, ratio, sh_order=10)
        assert_equal(len(w) > 0, True)

    with warnings.catch_warnings(record=True) as w:

        ConstrainedSDTModel(gtab, ratio, sh_order=8)
        assert_equal(len(w) > 0, False)

    csd_fit = csd.fit(np.zeros_like(S))
    fodf = csd_fit.odf(sphere)
    assert_array_equal(fodf, np.zeros_like(fodf))

    odf_sh = np.zeros_like(fodf)
    odf_sh[1] = np.nan

    fodf, _ = odf_deconv(odf_sh, csd.R, csd.B_reg)
    assert_array_equal(fodf, np.zeros_like(fodf))


def test_odf_sh_to_sharp():
    SNR = None
    S0 = 1
    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))

    S, _ = multi_tensor(gtab, mevals, S0, angles=[(10, 0), (100, 0)],
                        fractions=[50, 50], snr=SNR)

    sphere = default_sphere

    qb = QballModel(gtab, sh_order=8, assume_normed=True)

    qbfit = qb.fit(S)
    odf_gt = qbfit.odf(sphere)

    Z = np.linalg.norm(odf_gt)

    odfs_gt = np.zeros((3, 1, 1, odf_gt.shape[0]))
    odfs_gt[:, :, :] = odf_gt[:]

    odfs_sh = sf_to_sh(odfs_gt, sphere, sh_order=8, basis_type=None)

    odfs_sh /= Z

    fodf_sh = odf_sh_to_sharp(odfs_sh, sphere, basis=None, ratio=3 / 15.,
                              sh_order=8, lambda_=1., tau=0.1)

    fodf = sh_to_sf(fodf_sh, sphere, sh_order=8, basis_type=None)

    directions2, _, _ = peak_directions(fodf[0, 0, 0], sphere)

    assert_equal(directions2.shape[0], 2)


def test_forward_sdeconv_mat():
    _, n = sph_harm_ind_list(4)
    mat = forward_sdeconv_mat(np.array([0, 2, 4]), n)
    expected = np.diag([0, 2, 2, 2, 2, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4])
    npt.assert_array_equal(mat, expected)

    sh_order = 8
    expected_size = (sh_order + 1) * (sh_order + 2) / 2
    r_rh = np.arange(0, sh_order + 1, 2)
    m, n = sph_harm_ind_list(sh_order)
    mat = forward_sdeconv_mat(r_rh, n)
    npt.assert_equal(mat.shape, (expected_size, expected_size))
    npt.assert_array_equal(mat.diagonal(), n)

    # Odd spherical harmonic degrees should raise a ValueError
    n[2] = 3
    npt.assert_raises(ValueError, forward_sdeconv_mat, r_rh, n)


def test_r2_term_odf_sharp():
    SNR = None
    S0 = 1
    angle = 45  # 45 degrees is a very tight angle to disentangle

    _, fbvals, fbvecs = get_fnames('small_64D')  # get_fnames('small_64D')

    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)

    sphere = default_sphere
    gtab = gradient_table(bvals, bvecs)
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))

    angles = [(0, 0), (angle, 0)]

    S, _ = multi_tensor(gtab, mevals, S0, angles=angles,
                        fractions=[50, 50], snr=SNR)

    odf_gt = multi_tensor_odf(sphere.vertices, mevals, angles, [50, 50])
    odfs_sh = sf_to_sh(odf_gt, sphere, sh_order=8, basis_type=None)
    fodf_sh = odf_sh_to_sharp(odfs_sh, sphere, basis=None, ratio=3 / 15.,
                              sh_order=8, lambda_=1., tau=0.1, r2_term=True)
    fodf = sh_to_sf(fodf_sh, sphere, sh_order=8, basis_type=None)

    directions_gt, _, _ = peak_directions(odf_gt, sphere)
    directions, _, _ = peak_directions(fodf, sphere)

    ang_sim = angular_similarity(directions_gt, directions)
    assert_equal(ang_sim > 1.9, True)
    assert_equal(directions.shape[0], 2)

    # This should pass as well
    sdt_model = ConstrainedSDTModel(gtab, ratio=3/15., sh_order=8)
    sdt_fit = sdt_model.fit(S)
    fodf = sdt_fit.odf(sphere)

    directions_gt, _, _ = peak_directions(odf_gt, sphere)
    directions, _, _ = peak_directions(fodf, sphere)
    ang_sim = angular_similarity(directions_gt, directions)
    assert_equal(ang_sim > 1.9, True)
    assert_equal(directions.shape[0], 2)


def test_csd_predict():
    """
    Test prediction API
    """
    SNR = 100
    S0 = 1
    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))
    angles = [(0, 0), (60, 0)]
    S, _ = multi_tensor(gtab, mevals, S0, angles=angles,
                        fractions=[50, 50], snr=SNR)
    sphere = small_sphere
    multi_tensor_odf(sphere.vertices, mevals, angles, [50, 50])
    response = (np.array([0.0015, 0.0003, 0.0003]), S0)

    csd = ConstrainedSphericalDeconvModel(gtab, response)
    csd_fit = csd.fit(S)

    # Predicting from a fit should give the same result as predicting from a
    # model, S0 is 1 by default
    prediction1 = csd_fit.predict()
    prediction2 = csd.predict(csd_fit.shm_coeff)
    npt.assert_array_equal(prediction1, prediction2)
    npt.assert_array_equal(prediction1[..., gtab.b0s_mask], 1.)

    # Same with a different S0
    prediction1 = csd_fit.predict(S0=123.)
    prediction2 = csd.predict(csd_fit.shm_coeff, S0=123.)
    npt.assert_array_equal(prediction1, prediction2)
    npt.assert_array_equal(prediction1[..., gtab.b0s_mask], 123.)

    # For "well behaved" coefficients, the model should be able to find the
    # coefficients from the predicted signal.
    coeff = np.random.random(csd_fit.shm_coeff.shape) - .5
    coeff[..., 0] = 10.
    S = csd.predict(coeff)
    csd_fit = csd.fit(S)
    npt.assert_array_almost_equal(coeff, csd_fit.shm_coeff)

    # Test predict on nd-data set
    S_nd = np.zeros((2, 3, 4, S.size))
    S_nd[:] = S
    fit = csd.fit(S_nd)
    predict1 = fit.predict()
    predict2 = csd.predict(fit.shm_coeff)
    npt.assert_array_almost_equal(predict1, predict2)


def test_csd_predict_multi():
    """
    Check that we can predict reasonably from multi-voxel fits:

    """
    S0 = 123.
    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)
    response = (np.array([0.0015, 0.0003, 0.0003]), S0)
    csd = ConstrainedSphericalDeconvModel(gtab, response)
    coeff = np.random.random(45) - .5
    coeff[..., 0] = 10.
    S = csd.predict(coeff, S0=123.)
    multi_S = np.array([[S, S], [S, S]])
    csd_fit_multi = csd.fit(multi_S)
    S0_multi = np.mean(multi_S[..., gtab.b0s_mask], -1)
    pred_multi = csd_fit_multi.predict(S0=S0_multi)
    npt.assert_array_almost_equal(pred_multi, multi_S)


def test_sphere_scaling_csdmodel():
    """Check that mirroring regularization sphere does not change the result of
    the model"""
    _, fbvals, fbvecs = get_fnames('small_64D')

    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)

    gtab = gradient_table(bvals, bvecs)
    mevals = np.array(([0.0015, 0.0003, 0.0003],
                       [0.0015, 0.0003, 0.0003]))

    angles = [(0, 0), (60, 0)]

    S, _ = multi_tensor(gtab, mevals, 100., angles=angles,
                        fractions=[50, 50], snr=None)

    hemi = small_sphere
    sphere = hemi.mirror()

    response = (np.array([0.0015, 0.0003, 0.0003]), 100)
    model_full = ConstrainedSphericalDeconvModel(gtab, response,
                                                 reg_sphere=sphere)
    model_hemi = ConstrainedSphericalDeconvModel(gtab, response,
                                                 reg_sphere=hemi)
    csd_fit_full = model_full.fit(S)
    csd_fit_hemi = model_hemi.fit(S)

    assert_array_almost_equal(csd_fit_full.shm_coeff, csd_fit_hemi.shm_coeff)


def test_default_lambda_csdmodel():
    """We check that the default value of lambda is the expected value with
    the symmetric362 sphere. This value has empirically been found to work well
    and changes to this default value should be discussed with the dipy team.
    """
    expected_lambda = {4: 27.5230088, 8: 82.5713865, 16: 216.0843135}
    expected_warnings = {4: 0, 8: 0, 16: 1}
    sphere = default_sphere

    # Create gradient table
    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)

    # Some response function
    response = (np.array([0.0015, 0.0003, 0.0003]), 100)

    for sh_order, expected, e_warn in zip(expected_lambda.keys(),
                                          expected_lambda.values(),
                                          expected_warnings.values()):
        with warnings.catch_warnings(record=True) as w:
            model_full = ConstrainedSphericalDeconvModel(gtab, response,
                                                         sh_order=sh_order,
                                                         reg_sphere=sphere)
            npt.assert_equal(len(w), e_warn)
            if e_warn:
                npt.assert_(issubclass(w[0].category, UserWarning))
                npt.assert_("Number of parameters required " in str(w[0].
                                                                    message))

        B_reg, _, _ = real_sym_sh_basis(sh_order, sphere.theta, sphere.phi)
        npt.assert_array_almost_equal(model_full.B_reg, expected * B_reg)


def test_csd_superres():
    """ Check the quality of csdfit with high SH order. """
    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)

    # img, gtab = read_stanford_hardi()
    evals = np.array([[1.5, .3, .3]]) * [[1.], [1.]] / 1000.
    S, sticks = multi_tensor(gtab, evals, snr=None, fractions=[55., 45.])

    with warnings.catch_warnings(record=True) as w:
        warnings.filterwarnings(action="always",
                                message="Number of parameters required.*",
                                category=UserWarning)
        model16 = ConstrainedSphericalDeconvModel(gtab, (evals[0], 3.),
                                                  sh_order=16)
        assert_greater_equal(len(w), 1)
        npt.assert_(issubclass(w[-1].category, UserWarning))

    fit16 = model16.fit(S)

    sphere = HemiSphere.from_sphere(get_sphere('symmetric724'))
    # print local_maxima(fit16.odf(default_sphere), default_sphere.edges)
    d, v, ind = peak_directions(fit16.odf(sphere), sphere,
                                relative_peak_threshold=.2,
                                min_separation_angle=0)

    # Check that there are two peaks
    assert_equal(len(d), 2)

    # Check that peaks line up with sticks
    cos_sim = abs((d * sticks).sum(1)) ** .5
    assert_(all(cos_sim > .99))


def test_csd_convergence():
    """ Check existence of `convergence` keyword in CSD model """
    _, fbvals, fbvecs = get_fnames('small_64D')
    bvals, bvecs = read_bvals_bvecs(fbvals, fbvecs)
    gtab = gradient_table(bvals, bvecs)

    evals = np.array([[1.5, .3, .3]]) * [[1.], [1.]] / 1000.
    S, sticks = multi_tensor(gtab, evals, snr=None, fractions=[55., 45.])

    model_w_conv = ConstrainedSphericalDeconvModel(gtab, (evals[0], 3.),
                                                   sh_order=8, convergence=50)
    model_wo_conv = ConstrainedSphericalDeconvModel(gtab, (evals[0], 3.),
                                                    sh_order=8)

    assert_equal(model_w_conv.fit(S).shm_coeff, model_wo_conv.fit(S).shm_coeff)


if __name__ == '__main__':
    run_module_suite()
