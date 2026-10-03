"""Synthetic fixtures: python -m unittest discover -s tests -p test_preprocess_coffee.py -v"""
import argparse
import contextlib
import io
import random
import tempfile
import unittest
import sys
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import preprocess_coffee as p


class PreprocessingTests(unittest.TestCase):
    def test_hash_index_matches_brute_force(self):
        rng = random.Random(123)
        values = {str(i): rng.getrandbits(64) for i in range(200)}
        for radius in (0, 1, 4, 8):
            index = p.HashIndex(radius)
            for key, value in values.items():
                index.add(key, value)
            for value in list(values.values())[:10]:
                variants = [value, value ^ 7]
                expected = {k: min((h ^ v).bit_count() for v in variants)
                            for k, h in values.items()
                            if min((h ^ v).bit_count() for v in variants) <= radius}
                self.assertEqual(index.query(variants), expected)

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory(prefix='coffee-preprocess-test-') as tmp:
            root = Path(tmp)
            raw, plantdoc, own, output = [root / name for name in ('raw', 'plantdoc', 'own', 'out')]
            rng = np.random.default_rng(42)

            def texture():
                return Image.fromarray(rng.integers(30, 180, (40, 64, 3), dtype=np.uint8))

            def save(image, path, **options):
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path, **options)

            base = texture()
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('Comment', 'private metadata must disappear')
            save(base, raw/'Leaf rust'/'original.png', pnginfo=metadata)
            save(base, raw/'Leaf rust'/'exact_copy.png')
            save(base.transpose(Image.Transpose.ROTATE_90), raw/'Leaf rust'/'rotated.png')
            save(base.transpose(Image.Transpose.FLIP_LEFT_RIGHT), raw/'Leaf rust'/'flipped.png')
            # A brightness change is not pixel-identical, but is a near-duplicate candidate.
            near = Image.fromarray(np.asarray(base) + np.uint8(5))
            save(near, raw/'Leaf rust'/'near.png')
            for i in range(4):
                save(texture(), raw/'Leaf rust'/f'other_{i}.png')
            save(texture(), raw/'Cerscospora'/'valid.png')
            (raw/'Cerscospora'/'broken.jpg').write_bytes(b'not an image')
            # Cross-label exact copies are quarantined, not relabelled silently.
            conflict = texture()
            save(conflict, raw/'Healthy'/'conflict.png')
            save(conflict, raw/'Phoma'/'conflict.png')
            save(texture(), raw/'Miner'/'valid.png')
            save(texture(), plantdoc/'PlantDoc-Dataset-master'/'train'/'Apple leaf'/'valid.png')
            save(texture(), plantdoc/'PlantDoc-Dataset-master'/'PlantDoc_Examples.png')
            # A held-out duplicate wins over training eligibility and retains own provenance.
            overlap = texture()
            save(overlap, raw/'Leaf rust'/'overlap.png')
            save(overlap, own/'Rust'/'overlap.png')
            # Different viewpoints of one known physical leaf must share a group.
            save(texture(), own/'Healthy'/'view1.png')
            save(texture(), own/'Healthy'/'view2.png')
            own_groups = root/'own_groups.csv'
            p.write_csv(own_groups, ['source_path','leaf_id'], [
                dict(source_path='Healthy/view1.png', leaf_id='leaf-001'),
                dict(source_path='Healthy/view2.png', leaf_id='leaf-001')])
            exif = Image.Exif()
            exif[274] = 6
            exif[270] = 'private description'
            save(texture(), own/'unknown'/'oriented.jpg', exif=exif)
            before = {str(f): p.sha(f.read_bytes()) for d in (raw,plantdoc,own)
                      for f in d.rglob('*') if f.is_file()}
            args = argparse.Namespace(jmuben=raw, plantdoc=plantdoc, own_photos=own,
                                      own_groups=own_groups, output=output, radius=4)
            with contextlib.redirect_stdout(io.StringIO()):
                p.prepare(args)
            inventory = p.read_csv(output/'inventory.csv')
            clean = [r for r in inventory if r['status']=='clean']
            self.assertEqual(len(p.read_csv(output/'duplicate_map.csv')), 3)
            rejected = p.read_csv(output/'rejected_files.csv')
            self.assertEqual(len(rejected), 2)
            self.assertTrue(any(r['reason']=='outside_plantdoc_class_tree' for r in rejected))
            oriented = next(r for r in clean if r['source_path'].endswith('oriented.jpg'))
            self.assertEqual((oriented['width'],oriented['height']), ('40','64'))
            self.assertEqual((oriented['original_width'],oriented['original_height']), ('64','40'))
            self.assertTrue(any(r['dataset_id']=='jmuben' and r['label_en']=='Cercospora' for r in clean))
            originals = [r for r in clean if r['source_path'].endswith(
                ('original.png','exact_copy.png','rotated.png','flipped.png'))]
            self.assertEqual(len({r['pixel_group'] for r in originals}), 1)
            candidates = p.read_csv(output/'grouping_review.csv')
            self.assertTrue(candidates)
            split_args = argparse.Namespace(output=output, name='pending', seed=42, same_leaf=None)
            with contextlib.redirect_stdout(io.StringIO()):
                p.split(split_args)
            exclusions = p.read_csv(output/'pending'/'excluded_from_splits.csv')
            self.assertTrue(any(r['reason']=='pending_near_duplicate_review' for r in exclusions))
            self.assertTrue(any(r['reason']=='conflicting_labels' for r in exclusions))
            near_id = next(r['image_id'] for r in clean if r['source_path'].endswith('near.png'))
            original_ids = {r['image_id'] for r in originals}
            found_near = False
            for pair in candidates:
                endpoints = {pair['image_a'], pair['image_b']}
                same = near_id in endpoints and bool(endpoints & original_ids)
                pair['decision'] = 'same_leaf' if same else 'different_leaf'
                found_near |= same
            self.assertTrue(found_near)
            p.write_csv(output/'grouping_review.csv', p.PAIR_FIELDS, candidates)
            split_args.name = 'approved'
            with contextlib.redirect_stdout(io.StringIO()):
                p.split(split_args)
            manifest = p.read_csv(output/'approved'/'images_manifest.csv')
            near_rows = [r for r in manifest if r['image_id'] in original_ids | {near_id}]
            self.assertEqual(len({r['source_group_id'] for r in near_rows}), 1)
            self.assertEqual(len({r['split'] for r in near_rows}), 1)
            self.assertTrue(all(r['split']=='test' for r in manifest if r['role']!='train'))
            heldout = next(r for r in manifest if r['source_path'].endswith('overlap.png'))
            self.assertEqual((heldout['dataset_id'],heldout['split']), ('own_photos','test'))
            own_views = [r for r in manifest if r['source_path'].endswith(('view1.png','view2.png'))]
            self.assertEqual(len({r['source_group_id'] for r in own_views}), 1)
            split_args.name = 'repeat'
            with contextlib.redirect_stdout(io.StringIO()):
                p.split(split_args)
            self.assertEqual((output/'approved'/'images_manifest.csv').read_bytes(),
                             (output/'repeat'/'images_manifest.csv').read_bytes())
            for filename, digest in before.items():
                self.assertEqual(p.sha(Path(filename).read_bytes()), digest)
            with self.assertRaises(ValueError):
                p.prepare(args)  # A rerun cannot overwrite output or review decisions.
            p.write_csv(output/'grouping_review.csv', p.PAIR_FIELDS, candidates[:-1])
            with self.assertRaises(ValueError):
                p.split(split_args)  # Removing unresolved candidates cannot bypass review.
            tampered = output/manifest[0]['file_path']
            tampered.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                p.verify(output, output/'approved'/'images_manifest.csv')


if __name__ == '__main__':
    unittest.main()
