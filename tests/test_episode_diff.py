import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from episode_diff import (
    EpisodeKey,
    compute_missing,
    extract_episode_key,
    select_incremental_files,
)


def keys(title, season, episodes):
    return {
        EpisodeKey(title_key=title.casefold(), season=season, episode=episode)
        for episode in episodes
    }


class EpisodeDifferenceTests(unittest.TestCase):
    def test_extracts_common_episode_markers(self):
        self.assertEqual(
            extract_episode_key("Show.S02E003.1080P.mkv", "show"),
            EpisodeKey("show", 2, 3),
        )
        self.assertEqual(
            extract_episode_key("Show EP12.mp4", "show", default_season=1),
            EpisodeKey("show", 1, 12),
        )
        self.assertEqual(
            extract_episode_key("Show 第18集.mkv", "show", default_season=1),
            EpisodeKey("show", 1, 18),
        )
        self.assertEqual(
            extract_episode_key("01 4K.mp4", "show"),
            EpisodeKey("show", 1, 1),
        )
        self.assertEqual(
            extract_episode_key("91 4K.mp4", "牧神记"),
            EpisodeKey("牧神记", 1, 91),
        )

    def test_absolute_episode_scheme_matches_across_season_labels(self):
        # Remote encodes absolute ep numbers inside multi-season SxxExx.
        remote = {
            EpisodeKey("mushen", 1, 90),
            EpisodeKey("mushen", 4, 91),
            EpisodeKey("mushen", 4, 92),
        }
        local = {
            EpisodeKey("mushen", 1, ep) for ep in range(1, 92)
        }
        self.assertEqual(
            compute_missing(remote, local, set(), set()),
            {EpisodeKey("mushen", 4, 92)},
        )

    def test_update_returns_only_new_episode(self):
        remote = keys("凡人修仙传", 1, [118, 119, 120])
        local = keys("凡人修仙传", 1, [1, 118, 119])

        self.assertEqual(
            compute_missing(remote, local, set(), set()),
            keys("凡人修仙传", 1, [120]),
        )

    def test_active_and_planned_episodes_are_not_returned(self):
        remote = keys("show", 2, [3, 4])

        self.assertEqual(
            compute_missing(
                remote,
                set(),
                keys("show", 2, [3]),
                keys("show", 2, [4]),
            ),
            set(),
        )

    def test_selection_contains_only_wanted_files(self):
        result = select_incremental_files(
            [
                {"file_name": "Show.S01E119.mkv", "dir": False},
                {"file_name": "Show.S01E120.mkv", "dir": False},
                {"file_name": "Show.S01E120.ass", "dir": False},
            ],
            wanted=keys("show", 1, [120]),
            title_key="show",
            default_season=1,
        )

        self.assertTrue(result["selectable"])
        self.assertEqual(
            result["files"],
            ["Show.S01E120.ass", "Show.S01E120.mkv"],
        )

    def test_unparseable_collection_is_not_incrementally_selectable(self):
        result = select_incremental_files(
            [{"file_name": "全集文件夹", "dir": True}],
            wanted=keys("show", 1, [12]),
            title_key="show",
            default_season=1,
        )

        self.assertEqual(result, {"selectable": False, "files": []})


if __name__ == "__main__":
    unittest.main()
