"""
Test desispec.workflow.calibration_selection
"""

import os
import unittest

import numpy as np
from astropy.table import Table, vstack

class TestWorkflowCalibrationSelection(unittest.TestCase):
    """Test desispec.workflow.calibration_selection
    """

    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        pass

    def _make_arcset_etable(self, narcsperset=5, expid_offset=0):
        ntotalarcs = 2 * narcsperset
        arcset = Table()

        expids = list(np.arange(expid_offset, expid_offset + narcsperset)) \
                 + list(np.arange(expid_offset + 2 + narcsperset,
                                  expid_offset + 2 + ntotalarcs))
        arcset["EXPID"] = expids

        seqnums = list(1 + np.arange(ntotalarcs))
        arcset['SEQNUM'] = seqnums

        seqtots = list(np.ones(narcsperset, dtype=int) * narcsperset) \
                  + list(np.ones(narcsperset, dtype=int) * narcsperset)
        arcset['SEQTOT'] = seqtots

        arcset['LASTSTEP'] = ['ignore'] * ntotalarcs
        arcset['LASTSTEP'][:] = 'all'
        arcset['BADCAMWORD'] = ['b0123456789r0123456789'] * ntotalarcs
        arcset['BADCAMWORD'][:] = ''
        arcset['BADAMPS'] = ['b0123456789r0123456789'] * ntotalarcs
        arcset['BADAMPS'][:] = ''

        arcset['EXPTIME'] = [5.0] * narcsperset + [30.1] * narcsperset

        arcset['PROGRAM'] = ['calib short arcs all'] * narcsperset \
                            + ['calib long arcs cd+xe'] * narcsperset

        obs = ['arc'] * ntotalarcs
        arcset['OBSTYPE'] = obs

        return arcset

    def _make_flatset_etable(self, nflatsperset=3, nflatsets=4,
                            flatflatgap=3, expid_offset=0):
        flatset = Table()
        ncte = 3
        nexps = nflatsperset * nflatsets + ncte
        flatitteroffset = nflatsperset + flatflatgap - 1

        expids, seqnums, seqtots, progs = list(), list(), list(), list()
        for fset in range(nflatsets):
            fulloffset = expid_offset + fset * flatitteroffset
            expids += list(np.arange(fulloffset, fulloffset + nflatsperset))
            seqnums += list(1 + np.arange(nflatsperset))
            seqtots += list(np.ones(nflatsperset, dtype=int) * nflatsperset)
            progs += [f'calib desi-calib-0{fset} leds only'] * nflatsperset

        flatset["EXPID"] = expids + list(range(np.max(expids)+1,np.max(expids)+ncte+1))
        flatset['SEQNUM'] = seqnums + [1]*ncte
        flatset['SEQTOT'] = seqtots + [1]*ncte
        flatset['PROGRAM'] = progs + ['led03 flat for cte check']*ncte
        flatset['EXPTIME'] = [120.0] * (nflatsperset*nflatsets) + [1.0]*ncte

        flatset['LASTSTEP'] = ['ignore'] * nexps
        flatset['LASTSTEP'][:] = 'all'
        flatset['BADCAMWORD'] = ['b0123456789r0123456789'] * nexps
        flatset['BADCAMWORD'][:] = ''
        flatset['BADAMPS'] = ['b0123456789r0123456789'] * nexps
        flatset['BADAMPS'][:] = ''
        flatset['OBSTYPE'] = ['flat'] * nexps

        return flatset

    def _make_arcflatset_etable(self, narcsperset=5, nflatsperset=3, nflatsets=4,
                               flatflatgap=3, arcflatgap=5, expid_offset=0):
        arcset = self._make_arcset_etable(narcsperset=narcsperset,
                                    expid_offset=expid_offset)
        flat_offset = int(np.max(arcset['EXPID']) + arcflatgap)
        flatset = self._make_flatset_etable(nflatsperset=nflatsperset,
                                      nflatsets=nflatsets,
                                      flatflatgap=flatflatgap,
                                      expid_offset=flat_offset)
        arcflatset = vstack([arcset, flatset])
        arcflatset.sort(['EXPID'])
        return arcflatset

    def _get_cleaned_table(self, tab):
        from desispec.workflow.calibration_selection import \
            select_valid_calib_exposures
        cleaned_table, exptypes = select_valid_calib_exposures(tab)
        return cleaned_table[exptypes!='cteflat']
    
    def _test_tables_equal(self, tab1, tab2):
        self.assertTrue(len(tab1) == len(tab2))
        self.assertTrue(np.all(np.array(tab1['EXPID']) == np.array(tab2['EXPID'])))

    def test_find_best_arc_flat_sets(self):
        from desispec.workflow.calibration_selection import \
            find_best_arc_flat_sets
        ## Two good sets
        ## Should select first set since it came first
        set1 = self._make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        for erow in vstack([set1, set2]):
            print(list(erow))
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First good, second has ignored long arc
        ## Should select first set since long arcs don't matter
        # set1 = make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['LASTSTEP'][7] = 'ignore'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First good, second has bad short arc
        ## Should select first set since want full set of short arcs
        # set1 = make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['LASTSTEP'][2] = 'ignore'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First good, second has exposures with a badc camera
        ## Should select first set since second has some bad data
        # set1 = make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['BADCAMWORD'][::4] = 'r3'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First good, second has bad exp and bad cams
        ## Should select first set since second has multiple issues
        # set1 = make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['LASTSTEP'][2] = 'ignore'
        set2['BADCAMWORD'][::4] = 'r3'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First good, second has bad amp
        ## Should select first set since second has a bad amp
        # set1 = make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['BADAMPS'][14] = 'b3A'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First good, second has bad exp and bad amps
        ## Should select first set since second has multiple issues
        # set1 = make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['LASTSTEP'][2] = 'ignore'
        set2['BADAMPS'][14] = 'b3A'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## The same set of tests, but setting the first table with the issue
        ## to ensure it now picks up the second

        ## First has bad long arc, second good
        ## Should select first set since we don't care about log cals
        set1 = self._make_arcflatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set1['LASTSTEP'][7] = 'ignore'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First has bad short arc, second good
        ## Should select second set since first has bad exp
        set1 = self._make_arcflatset_etable()
        # set2 = make_arcflatset_etable(expid_offset=40)
        set1['LASTSTEP'][2] = 'ignore'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First has bad cameras, second good
        ## Should select second set since first has bad cameras
        set1 = self._make_arcflatset_etable()
        # set2 = make_arcflatset_etable(expid_offset=40)
        set1['BADCAMWORD'][::4] = 'r3'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First has bad exposure and cameras, second good
        ## Should select second set since first has issues
        set1 = self._make_arcflatset_etable()
        # set2 = make_arcflatset_etable(expid_offset=40)
        set1['LASTSTEP'][2] = 'ignore'
        set1['BADCAMWORD'][::4] = 'r3'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First has bad cameras, second good
        ## Should select second set since first has bad cameras
        set1 = self._make_arcflatset_etable()
        # set2 = make_arcflatset_etable(expid_offset=40)
        set1['BADAMPS'][14] = 'b3A'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First has bad exposure and bad amps, second good
        ## Should select second set since first has issues
        set1 = self._make_arcflatset_etable()
        # set2 = make_arcflatset_etable(expid_offset=40)
        set1['LASTSTEP'][2] = 'ignore'
        set1['BADAMPS'][14] = 'b3A'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## Now test cases with a complete set and an incomplete set

        ## First only arcs, second complete
        ## Should select second set since complete
        set1 = self._make_arcset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First complete with bad exposure, second only arcs
        ## Should select first set since one bad arc is acceptable and has flats
        set1 = self._make_arcflatset_etable()
        set2 = self._make_arcset_etable(expid_offset=50)
        set1['LASTSTEP'][2] = 'ignore'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First complete with first two short arcs bad, second only arc set
        ## Should select first set since can use as few as 3 arcs for fit
        set1 = self._make_arcflatset_etable()
        set2 = self._make_arcset_etable(expid_offset=50)
        set1['LASTSTEP'][:2] = 'ignore'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First complete with first three short arcs bad, second only arc set
        ## Should select second set since 2 short arcs aren't enough
        set1 = self._make_arcflatset_etable()
        set2 = self._make_arcset_etable(expid_offset=50)
        set1['LASTSTEP'][:3] = 'ignore'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First just flats, second full set but with issues
        ## Should select second set since can use 4 arcs and one bad amp is okay
        set1 = self._make_flatset_etable()
        set2 = self._make_arcflatset_etable(expid_offset=50)
        set2['LASTSTEP'][2] = 'ignore'
        set2['BADAMPS'][14] = 'b3A'
        expected = self. _get_cleaned_table(set2)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

        ## First full set with issues, second just flat
        ## Should select first set since 4 good arcs is fine and one badamp is okay
        set1 = self._make_arcflatset_etable()
        set2 = self._make_flatset_etable(expid_offset=50)
        set1['LASTSTEP'][2] = 'ignore'
        set1['BADAMPS'][14] = 'b3A'
        expected = self. _get_cleaned_table(set1)
        best = find_best_arc_flat_sets(vstack([set1, set2]))
        self._test_tables_equal(expected, best)

    def test_extra_badcals(self):
        """
        Test case where extra cals exist but are flagged as bad
        """
        from desispec.workflow.calibration_selection import \
            find_best_arc_flat_sets
        goodset = self._make_arcflatset_etable()
        badset = self._make_arcflatset_etable()
        badset['LASTSTEP'] = 'ignore'
        badarcs = badset[badset['OBSTYPE'] == 'arc']
        badflats = badset[badset['OBSTYPE'] == 'flat']
        expected = self._get_cleaned_table(goodset)

        # good before bad
        testset = vstack([goodset, badset])
        testset['EXPID'] = 100+np.arange(len(testset))
        best = find_best_arc_flat_sets(testset)
        if len(best) == len(expected):
            expected['EXPID'] = best['EXPID']
        self._test_tables_equal(expected, best)

        # bad before good
        testset = vstack([badset, goodset])
        testset['EXPID'] = 100+np.arange(len(testset))
        best = find_best_arc_flat_sets(testset)
        if len(best) == len(expected):
            expected['EXPID'] = best['EXPID']
        self._test_tables_equal(expected, best)

        # bad arcs before good set
        testset = vstack([badarcs, goodset])
        testset['EXPID'] = 100+np.arange(len(testset))
        best = find_best_arc_flat_sets(testset)
        if len(best) == len(expected):
            expected['EXPID'] = best['EXPID']
        self._test_tables_equal(expected, best)

        # bad arcs after good set
        testset = vstack([goodset, badarcs])
        testset['EXPID'] = 100+np.arange(len(testset))
        best = find_best_arc_flat_sets(testset)
        if len(best) == len(expected):
            expected['EXPID'] = best['EXPID']
        self._test_tables_equal(expected, best)

        # bad flats before good set
        testset = vstack([badflats, goodset])
        testset['EXPID'] = 100+np.arange(len(testset))
        best = find_best_arc_flat_sets(testset)
        if len(best) == len(expected):
            expected['EXPID'] = best['EXPID']
        self._test_tables_equal(expected, best)

        # bad flats after good set
        testset = vstack([goodset, badflats])
        testset['EXPID'] = 100+np.arange(len(testset))
        best = find_best_arc_flat_sets(testset)
        if len(best) == len(expected):
            expected['EXPID'] = best['EXPID']
        self._test_tables_equal(expected, best)


