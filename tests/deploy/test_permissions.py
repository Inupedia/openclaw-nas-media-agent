import unittest
from pathlib import Path

from deploy.installer.errors import DeploymentError
from deploy.installer.permissions import (
    PathStat,
    PermissionStrategy,
    RuntimeIdentity,
    choose_permission_plan,
)


class PermissionPlanTests(unittest.TestCase):
    def test_same_uid_uses_owner_mode_without_chown_or_acl(self):
        plan = choose_permission_plan(
            PathStat(
                path=Path("/volume2/downloads/.incoming"),
                uid=1000,
                gid=1000,
                mode=0o755,
                zone="incoming",
            ),
            RuntimeIdentity(uid=1000, gid=1000, groups=(1000,)),
            acl_supported=True,
        )
        self.assertEqual(plan.strategy, PermissionStrategy.SAME_UID)
        self.assertEqual(plan.mode, 0o750)
        self.assertEqual([change.action for change in plan.changes], ["set_mode"])
        self.assertEqual(plan.changes[0].mode, 0o750)

    def test_shared_gid_uses_group_write_mode(self):
        plan = choose_permission_plan(
            PathStat(
                path=Path("/volume2/downloads/.incoming"),
                uid=0,
                gid=2000,
                mode=0o750,
                zone="incoming",
            ),
            RuntimeIdentity(uid=1000, gid=1000, groups=(1000, 2000)),
            acl_supported=True,
        )
        self.assertEqual(plan.strategy, PermissionStrategy.SHARED_GID)
        self.assertEqual(plan.gid, 2000)
        self.assertEqual(plan.mode, 0o770)
        self.assertEqual([item.action for item in plan.changes], ["set_mode"])

    def test_acl_is_preferred_before_world_writable_fallback(self):
        plan = choose_permission_plan(
            PathStat(
                path=Path("/volume2/downloads/.incoming"),
                uid=0,
                gid=0,
                mode=0o750,
                zone="incoming",
            ),
            RuntimeIdentity(uid=1000, gid=1000, groups=(1000,)),
            acl_supported=True,
        )
        self.assertEqual(plan.strategy, PermissionStrategy.POSIX_ACL)
        self.assertEqual(plan.mode, 0o750)
        self.assertEqual(plan.acl_entries, ("u:1000:rwx",))
        self.assertEqual([item.action for item in plan.changes], ["set_acl"])

    def test_fallback_0777_is_limited_to_managed_download_zones(self):
        for zone in ("downloads_root", "incoming"):
            with self.subTest(zone=zone):
                plan = choose_permission_plan(
                    PathStat(
                        path=Path("/volume2/downloads"),
                        uid=0,
                        gid=0,
                        mode=0o750,
                        zone=zone,
                    ),
                    RuntimeIdentity(uid=1000, gid=1000, groups=(1000,)),
                    acl_supported=False,
                )
                self.assertEqual(
                    plan.strategy,
                    PermissionStrategy.WORLD_WRITABLE_FALLBACK,
                )
                self.assertEqual(plan.mode, 0o777)
                self.assertEqual(plan.changes[0].action, "set_mode")

    def test_formal_library_is_never_a_permission_target(self):
        with self.assertRaises(DeploymentError) as ctx:
            choose_permission_plan(
                PathStat(
                    path=Path("/volume2/media/Anime"),
                    uid=0,
                    gid=0,
                    mode=0o755,
                    zone="formal_library",
                ),
                RuntimeIdentity(uid=1000, gid=1000, groups=(1000,)),
                acl_supported=False,
            )
        self.assertEqual(ctx.exception.severity, "security_block")
        self.assertEqual(ctx.exception.code, "FORMAL_LIBRARY_PERMISSION_CHANGE")

    def test_ready_and_quarantine_are_agent_owned_not_aria2_targets(self):
        for zone in ("ready", "quarantine"):
            with self.subTest(zone=zone):
                plan = choose_permission_plan(
                    PathStat(
                        path=Path("/volume2/downloads") / f".{zone}",
                        uid=0,
                        gid=0,
                        mode=0o777,
                        zone=zone,
                    ),
                    RuntimeIdentity(uid=1000, gid=1000, groups=(1000,)),
                    acl_supported=False,
                )
                self.assertEqual(plan.strategy, PermissionStrategy.AGENT_OWNED)
                self.assertEqual(plan.uid, 0)
                self.assertEqual(plan.mode, 0o750)
                self.assertEqual([item.action for item in plan.changes], ["set_mode"])


if __name__ == "__main__":
    unittest.main()
