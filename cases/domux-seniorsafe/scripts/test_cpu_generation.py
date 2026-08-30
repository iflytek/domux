"""CPU adapter contract tests using tiny tensors, never model weights."""
import tempfile
import unittest
from pathlib import Path

try:
    import torch
    from run_transformers_cpu import generate, resolve_snapshot
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'optional CPU inference packages not installed')
class CpuAdapterTests(unittest.TestCase):
    def test_token_limit_without_eos_is_not_a_success(self):
        class Processor:
            def apply_chat_template(self, *args, **kwargs):
                return {'input_ids': torch.tensor([[99]])}

            def decode(self, *args, **kwargs):
                return 'turnOn|Light|*|*|*|Bedroom|*'

        class Model:
            generation_config = type('Config', (), {'eos_token_id': [2]})()

            def generate(self, **kwargs):
                return torch.tensor([[99, 1]])

        with self.assertRaises(ValueError):
            generate(Processor(), Model(), 'test', 1)

    def test_eos_at_token_limit_is_a_complete_response(self):
        class Processor:
            def apply_chat_template(self, *args, **kwargs):
                return {'input_ids': torch.tensor([[99]])}

            def decode(self, *args, **kwargs):
                return 'turnOn|Light|*|*|*|Bedroom|*'

        class Model:
            generation_config = type('Config', (), {'eos_token_id': [2]})()

            def generate(self, **kwargs):
                return torch.tensor([[99, 2]])

        output, _ = generate(Processor(), Model(), 'test', 1)
        self.assertEqual('turnOn|Light|*|*|*|Bedroom|*', output)

    def test_arbitrary_local_folder_cannot_claim_pinned_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                resolve_snapshot('iFlytekOpenSource/Domux', '0'*40, Path(temp))


if __name__ == '__main__':
    unittest.main()
