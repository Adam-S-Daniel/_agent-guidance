    def test_dependabot_cooldown(self):
        args = ("dependency-updates", "dependabot-cooldown")
        plain = ("version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: /\n"
                 "    schedule:\n      interval: weekly\n  - package-ecosystem: github-actions\n"
                 "    directory: /\n    schedule:\n      interval: weekly\n")
        self._check(*args, {".github/dependabot.yml": plain},
                    {"config-parses": True, "both-ecosystems": True,
                     "seven-day-cooldown": False, "major-cooldown-where-supported": False})
        both_major = plain.replace("interval: weekly\n", "interval: weekly\n    cooldown:\n"
                                   "      default-days: 7\n      semver-major-days: 30\n")
        self._check(*args, {".github/dependabot.yml": both_major},
                    {"config-parses": True, "both-ecosystems": True,
                     "seven-day-cooldown": True, "major-cooldown-where-supported": False})
        npm, actions = both_major.split("  - package-ecosystem: github-actions")
        good = npm + "  - package-ecosystem: github-actions" + actions.replace(
            "      semver-major-days: 30\n", "")
        self._check(*args, {".github/dependabot.yml": good}, {True})
