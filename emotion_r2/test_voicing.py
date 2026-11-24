import unittest
import numpy as np
from voicing import gate_from_probabilities

class VoicingGateTests(unittest.TestCase):
    def test_probability_and_share_thresholds(self):
        probabilities=np.r_[np.full(40,.5),np.full(160,.49)]
        result=gate_from_probabilities(probabilities,0,4)
        self.assertTrue(result['active'])
        self.assertEqual(result['voiced_frames'],40)
        probabilities[0]=.49
        self.assertFalse(gate_from_probabilities(probabilities,0,4)['active'])

    def test_padding_is_unvoiced(self):
        result=gate_from_probabilities(np.ones(10),0,.2)
        self.assertFalse(result['active'])
        self.assertEqual(result['total_frames'],200)
        self.assertEqual(result['voiced_frames'],10)

    def test_nonfinite_values_are_rejected(self):
        with self.assertRaises(ValueError): gate_from_probabilities([float('nan')],0,1)

if __name__=='__main__': unittest.main()
