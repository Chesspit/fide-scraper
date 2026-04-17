"""Tests for the age-matched sampling logic in seed_players.py."""

from collections import defaultdict

from scripts.seed_players import age_matched_sample, decade_bucket


def _make_player(fide_id, sex, rating, birth_year):
    return {
        "fide_id": fide_id,
        "name": f"Player {fide_id}",
        "federation": "TST",
        "sex": sex,
        "title": None,
        "women_title": None,
        "std_rating": rating,
        "birth_year": birth_year,
    }


class TestDecadeBucket:
    def test_1990(self):
        assert decade_bucket(1990) == 1990

    def test_1985(self):
        assert decade_bucket(1985) == 1980

    def test_2003(self):
        assert decade_bucket(2003) == 2000

    def test_none(self):
        assert decade_bucket(None) is None


class TestAgeMatchedSample:
    def setup_method(self):
        # Create 20 women: 10 from 1980s, 8 from 1990s, 2 from 2000s
        self.women = []
        for i in range(10):
            self.women.append(_make_player(1000 + i, "F", 2500, 1980 + i % 10))
        for i in range(8):
            self.women.append(_make_player(2000 + i, "F", 2500, 1990 + i % 10))
        for i in range(2):
            self.women.append(_make_player(3000 + i, "F", 2500, 2000 + i))

        # Create 200 men across decades
        self.men = []
        for decade in [1980, 1990, 2000]:
            for i in range(66):
                fid = decade * 100 + i
                self.men.append(_make_player(fid, "M", 2500, decade + i % 10))

    def test_sample_size(self):
        sampled = age_matched_sample(self.women, self.men, 50, seed=42)
        assert len(sampled) == 50

    def test_proportional_distribution(self):
        sampled = age_matched_sample(self.women, self.men, 100, seed=42)

        # Women distribution: 50% 1980s, 40% 1990s, 10% 2000s
        sampled_decades = defaultdict(int)
        for m in sampled:
            sampled_decades[decade_bucket(m["birth_year"])] += 1

        # Should be roughly proportional (±5 tolerance due to rounding)
        assert abs(sampled_decades[1980] - 50) <= 5
        assert abs(sampled_decades[1990] - 40) <= 5
        assert abs(sampled_decades[2000] - 10) <= 5

    def test_reproducibility_with_seed(self):
        sample_a = age_matched_sample(self.women, self.men, 50, seed=42)
        sample_b = age_matched_sample(self.women, self.men, 50, seed=42)

        ids_a = [m["fide_id"] for m in sample_a]
        ids_b = [m["fide_id"] for m in sample_b]
        assert ids_a == ids_b

    def test_different_seed_different_sample(self):
        sample_a = age_matched_sample(self.women, self.men, 50, seed=42)
        sample_b = age_matched_sample(self.women, self.men, 50, seed=99)

        ids_a = {m["fide_id"] for m in sample_a}
        ids_b = {m["fide_id"] for m in sample_b}
        assert ids_a != ids_b

    def test_all_sampled_are_men(self):
        sampled = age_matched_sample(self.women, self.men, 50, seed=42)
        assert all(m["sex"] == "M" for m in sampled)

    def test_no_duplicates(self):
        sampled = age_matched_sample(self.women, self.men, 50, seed=42)
        ids = [m["fide_id"] for m in sampled]
        assert len(ids) == len(set(ids))

    def test_sparse_decade_overflow(self):
        """When a decade has fewer men than needed, overflow goes to next decade."""
        # Only 2 men in 1980s, but women are mostly 1980s
        sparse_men = [
            _make_player(9001, "M", 2500, 1985),
            _make_player(9002, "M", 2500, 1988),
        ]
        # Add many men in 1990s as overflow target
        for i in range(100):
            sparse_men.append(_make_player(10000 + i, "M", 2500, 1990 + i % 10))

        sampled = age_matched_sample(self.women, sparse_men, 20, seed=42)
        assert len(sampled) == 20

        # Both 1980s men should be included
        ids_1980s = [m["fide_id"] for m in sampled if decade_bucket(m["birth_year"]) == 1980]
        assert 9001 in ids_1980s
        assert 9002 in ids_1980s
