#!/usr/bin/env python3
"""Unit tests for the agent-context generator."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate  # noqa: E402


def run_git(repo, *args):
    subprocess.run(
        ["git", "-C", repo, *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class GeneratorTestCase(unittest.TestCase):
    """Base class that builds a temporary git repository."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        run_git(self.repo, "init", "-q")
        run_git(self.repo, "config", "user.email", "test@example.com")
        run_git(self.repo, "config", "user.name", "Test")
        self.out = os.path.join(self.repo, ".out")
        os.makedirs(self.out, exist_ok=True)

    def add_file(self, rel_path, content):
        full = os.path.join(self.repo, rel_path)
        os.makedirs(os.path.dirname(full) or self.repo, exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(full, mode) as handle:
            handle.write(content)

    def commit_all(self):
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "test fixture")

    def generate(self, max_bytes=20480):
        return generate.generate(self.repo, self.out, max_bytes)

    def read(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as handle:
            return handle.read()


class ExclusionTests(GeneratorTestCase):
    """Secret-named and CONSOLIDATED_* files must never appear."""

    def test_excludes_secret_and_consolidated_files(self):
        self.add_file(".env", "SECRET=1\n")
        self.add_file(".env.production", "SECRET=2\n")
        self.add_file(".env.example", "SECRET=example\n")
        self.add_file("server.pem", "-----BEGIN-----\n")
        self.add_file("private.key", "keydata\n")
        self.add_file("id_rsa", "identity\n")
        self.add_file("debug.log", "log line\n")
        self.add_file("CONSOLIDATED_CODE.md", "# dump\n")
        self.add_file("CONSOLIDATED_DOCS.md", "# dump\n")
        self.add_file("keep.md", "# Kept file\n")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        listed = {
            line[2:].split(" - ", 1)[0]
            for line in repo_map.splitlines()
            if line.startswith("- ")
        }

        self.assertIn("keep.md", listed)
        self.assertIn(".env.example", listed)
        for excluded in (
            ".env",
            ".env.production",
            "server.pem",
            "private.key",
            "id_rsa",
            "debug.log",
            "CONSOLIDATED_CODE.md",
            "CONSOLIDATED_DOCS.md",
        ):
            self.assertNotIn(excluded, listed)


class DeterminismTests(GeneratorTestCase):
    """Two runs over the same tree must be byte-identical."""

    def test_deterministic_byte_identical_output(self):
        self.add_file("README.md", "# Readme\n")
        self.add_file("pkg/mod.py", '"""Module doc."""\n\n\ndef f(a, b=1):\n    return a\n')
        self.add_file("run.sh", "#!/bin/sh\n# entry point\ngreet() {\n  echo hi\n}\n")
        self.commit_all()

        out_a = os.path.join(self.repo, "out_a")
        out_b = os.path.join(self.repo, "out_b")
        generate.generate(self.repo, out_a, 20480)
        generate.generate(self.repo, out_b, 20480)

        for name in ("REPO_MAP.md", "CODE_INDEX.md"):
            with open(os.path.join(out_a, name), "rb") as handle:
                first = handle.read()
            with open(os.path.join(out_b, name), "rb") as handle:
                second = handle.read()
            self.assertEqual(first, second, name)

    def test_file_list_is_sorted(self):
        self.add_file("z.md", "# Z\n")
        self.add_file("a.md", "# A\n")
        self.add_file("m.md", "# M\n")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        self.assertLess(repo_map.index("a.md"), repo_map.index("m.md"))
        self.assertLess(repo_map.index("m.md"), repo_map.index("z.md"))


class BinarySkipTests(GeneratorTestCase):
    """Files with a NUL byte in the first 8 KB must be skipped."""

    def test_binary_skip(self):
        self.add_file("text.md", "# Text\n")
        self.add_file("blob.bin", b"header\x00binary\x00data")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        self.assertIn("text.md", repo_map)
        self.assertNotIn("blob.bin", repo_map)


class UnparsedTests(GeneratorTestCase):
    """A file that fails to parse is marked (unparsed) and must not abort."""

    def test_unparsed_handling(self):
        self.add_file("good.py", "def ok(x):\n    return x\n")
        self.add_file("broken.py", "def broken(:\n    this is not python\n")
        self.commit_all()

        self.generate()
        code_index = self.read("CODE_INDEX.md")

        self.assertIn("## broken.py", code_index)
        broken_section = code_index.split("## broken.py", 1)[1]
        self.assertIn("(unparsed)", broken_section.split("## ", 1)[0])
        self.assertIn("## good.py", code_index)
        self.assertIn("def ok(x)", code_index)


class DescriptionTests(GeneratorTestCase):
    """Descriptions are one line, at most 100 chars, with backticks removed."""

    def test_description_truncation_and_backtick_removal(self):
        heading = "# " + "`code` " + ("word " * 40)
        self.add_file("long.md", heading + "\n")
        self.add_file("ticks.md", "# has `backticks` here\n")
        self.add_file("doc.py", '"""First docstring line with `ticks`."""\n')
        self.add_file(
            "tool.sh",
            "#!/bin/bash\n"
            "# ------------------------------------------------------------------\n"
            "# Real shell summary\n",
        )
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        body = repo_map.split("-->", 1)[1]

        long_line = next(line for line in body.splitlines() if line.startswith("- long.md"))
        description = long_line.split(" - ", 1)[1]
        self.assertLessEqual(len(description), 100)

        self.assertNotIn("`", body)
        ticks_line = next(line for line in body.splitlines() if line.startswith("- ticks.md"))
        self.assertTrue(ticks_line.endswith("has backticks here"))
        py_line = next(line for line in body.splitlines() if line.startswith("- doc.py"))
        self.assertTrue(py_line.endswith("First docstring line with ticks."))
        sh_line = next(line for line in body.splitlines() if line.startswith("- tool.sh"))
        self.assertTrue(sh_line.endswith("Real shell summary"))

    def test_yaml_comment_fallback_skips_dividers(self):
        self.add_file("data.yml", "# ----------\n# Real yaml summary\nfoo: bar\n")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        line = next(line for line in repo_map.splitlines() if line.startswith("- data.yml"))
        self.assertTrue(line.endswith("Real yaml summary"), line)


class RepoMapGroupingTests(GeneratorTestCase):
    """Every file sits under its own directory heading; no heading repeats."""

    def _headings_and_files(self, repo_map):
        headings = []
        files = {}
        current = None
        for line in repo_map.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                headings.append(current)
                files.setdefault(current, [])
            elif line.startswith("- ") and current is not None:
                files[current].append(line[2:].split(" - ", 1)[0])
        return headings, files

    def _assert_grouped(self, repo_map):
        headings, files = self._headings_and_files(repo_map)
        # No directory heading repeats.
        self.assertEqual(len(headings), len(set(headings)), headings)
        # Every file sits under its own directory's heading.
        for heading, listed in files.items():
            expected_dir = "" if heading == "." else heading.rstrip("/")
            for path in listed:
                self.assertEqual(
                    os.path.dirname(path),
                    expected_dir,
                    "%s listed under %r but belongs to %r"
                    % (path, heading, os.path.dirname(path)),
                )
        return headings, files

    def test_each_file_under_its_own_directory(self):
        self.add_file("LICENSE", "license text\n")
        self.add_file("README.md", "# Root readme\n")
        self.add_file("install.sh", "#!/bin/sh\n# installer\n")
        self.add_file(".github/workflows/build.yml", "name: Build\non: push\njobs:\n  x:\n")
        self.add_file("hermes/README.md", "# Hermes\n")
        self.add_file("hermes/Dockerfile", "FROM scratch\n")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        headings, files = self._assert_grouped(repo_map)

        self.assertIn(".", headings)
        for root_file in ("LICENSE", "README.md", "install.sh"):
            self.assertIn(root_file, files["."])
        self.assertIn(".github/workflows", files)
        self.assertIn(".github/workflows/build.yml", files[".github/workflows"])
        self.assertIn("hermes", files)
        self.assertIn("hermes/README.md", files["hermes"])

    def test_heading_does_not_repeat_when_subdir_interleaves(self):
        self.add_file("a/x.md", "# X\n")
        self.add_file("a/b/y.md", "# Y\n")
        self.add_file("a/z.md", "# Z\n")
        self.add_file("root.md", "# Root\n")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        headings, files = self._assert_grouped(repo_map)

        self.assertEqual(headings.count("a"), 1)
        self.assertEqual(headings.count("a/b"), 1)
        self.assertEqual(headings.count("."), 1)
        self.assertIn("a/x.md", files["a"])
        self.assertIn("a/z.md", files["a"])
        self.assertIn("a/b/y.md", files["a/b"])
        self.assertIn("root.md", files["."])

    def test_github_paths_listed_without_description(self):
        self.add_file(".github/README.md", "# GitHub readme\n")
        self.add_file(".github/workflows/build.yml", "name: Build\non: push\njobs:\n  x:\n")
        self.commit_all()

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        listed = {
            line[2:].split(" - ", 1)[0]: line
            for line in repo_map.splitlines()
            if line.startswith("- ")
        }

        self.assertIn(".github/README.md", listed)
        self.assertEqual(listed[".github/README.md"], "- .github/README.md")
        self.assertIn(".github/workflows/build.yml", listed)
        self.assertEqual(
            listed[".github/workflows/build.yml"],
            "- .github/workflows/build.yml",
        )

        # Job ids still appear in CODE_INDEX.md.
        code_index = self.read("CODE_INDEX.md")
        section = code_index.split("## .github/workflows/build.yml", 1)[1]
        self.assertIn("- x", section)


class CodeIndexTests(GeneratorTestCase):
    """Signature, service, job, and stage extraction."""

    def test_python_signatures_without_defaults(self):
        self.add_file(
            "shapes.py",
            "def area(width, height=2):\n"
            "    return width * height\n"
            "\n"
            "class Circle:\n"
            "    def __init__(self, radius, pi=3.14):\n"
            "        self.radius = radius\n"
            "    async def scale(self, factor):\n"
            "        return factor\n",
        )
        self.commit_all()
        self.generate()
        code_index = self.read("CODE_INDEX.md")

        self.assertIn("def area(width, height)", code_index)
        self.assertNotIn("height=2", code_index)
        self.assertIn("class Circle()", code_index)
        self.assertIn("def __init__(self, radius, pi)", code_index)
        self.assertIn("async def scale(self, factor)", code_index)

    def test_shell_function_names(self):
        self.add_file(
            "tool.sh",
            "#!/bin/bash\n"
            "# a tool\n"
            "set -e\n"
            "first() {\n"
            "  echo one\n"
            "}\n"
            "function second {\n"
            "  echo two\n"
            "}\n",
        )
        self.commit_all()
        self.generate()
        code_index = self.read("CODE_INDEX.md")

        self.assertIn("- first()", code_index)
        self.assertIn("- second()", code_index)

    def test_compose_services(self):
        self.add_file(
            "docker-compose.yml",
            "version: '3'\n"
            "services:\n"
            "  web:\n"
            "    image: nginx\n"
            "  db:\n"
            "    image: postgres\n"
            "volumes:\n"
            "  data:\n",
        )
        self.commit_all()
        self.generate()
        code_index = self.read("CODE_INDEX.md")

        section = code_index.split("## docker-compose.yml", 1)[1].split("## ", 1)[0]
        self.assertIn("- web", section)
        self.assertIn("- db", section)
        self.assertNotIn("- volumes", section)

    def test_workflow_jobs(self):
        self.add_file(
            ".github/workflows/build.yml",
            "name: Build\n"
            "on: push\n"
            "jobs:\n"
            "  lint:\n"
            "    runs-on: ubuntu-latest\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n",
        )
        self.commit_all()
        self.generate()
        repo_map = self.read("REPO_MAP.md")
        code_index = self.read("CODE_INDEX.md")

        self.assertIn(".github/workflows/build.yml", repo_map)
        section = code_index.split("## .github/workflows/build.yml", 1)[1]
        self.assertIn("- lint", section)
        self.assertIn("- test", section)

    def test_other_yaml_top_level_keys(self):
        self.add_file("config.yml", "alpha: 1\nnested:\n  inner: 2\nbeta: 3\n")
        self.commit_all()
        self.generate()
        code_index = self.read("CODE_INDEX.md")

        section = code_index.split("## config.yml", 1)[1]
        self.assertIn("- alpha", section)
        self.assertIn("- nested", section)
        self.assertIn("- beta", section)
        self.assertNotIn("- inner", section)

    def test_dockerfile_from_lines_and_stages(self):
        self.add_file(
            "hermes/Dockerfile",
            "# base image\n"
            "FROM python:3.11-slim AS base\n"
            "RUN echo hi\n"
            "FROM base AS final\n",
        )
        self.commit_all()
        self.generate()
        code_index = self.read("CODE_INDEX.md")

        section = code_index.split("## hermes/Dockerfile", 1)[1]
        self.assertIn("- FROM python:3.11-slim AS base", section)
        self.assertIn("- stage: base", section)
        self.assertIn("- stage: final", section)


class SizeLimitTests(GeneratorTestCase):
    """Overflow exits non-zero and names the file and its size."""

    def test_size_overflow_raises_nonzero(self):
        self.add_file("README.md", "# Big\n" + ("content line\n" * 200))
        self.commit_all()

        with self.assertRaises(SystemExit) as context:
            self.generate(max_bytes=64)
        self.assertNotEqual(context.exception.code, 0)

    def test_size_overflow_subprocess_message(self):
        self.add_file("README.md", "# Big\n" + ("content line\n" * 200))
        self.commit_all()

        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "generate.py",
        )
        result = subprocess.run(
            [
                sys.executable,
                script,
                "--root",
                self.repo,
                "--output-dir",
                self.out,
                "--max-bytes",
                "64",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"REPO_MAP\.md is \d+ bytes")
        self.assertIn("exceeds the limit", result.stderr)


class SymlinkGuardTests(GeneratorTestCase):
    """Tracked symlinks must be excluded: never listed and never read."""

    def _tracked_mode(self, rel_path):
        """Return the git index mode (e.g. ``120000``) for ``rel_path``."""
        result = subprocess.run(
            ["git", "-C", self.repo, "ls-files", "-s", "--", rel_path],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        return result.stdout.split()[0]

    def test_symlink_outside_repo_is_neither_listed_nor_read(self):
        external_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, external_dir, True)
        outside = os.path.join(external_dir, "outside.txt")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write(
                "# OUTSIDE_HEADING_MARKER\n"
                "\n"
                "def outside_def_marker():\n"
                "    return 1\n"
            )

        # Two tracked symlinks to the same outside file. `leak.md` would make
        # REPO_MAP read the `# ` heading; `leak.py` would make CODE_INDEX read
        # the def name.
        os.symlink(outside, os.path.join(self.repo, "leak.md"))
        os.symlink(outside, os.path.join(self.repo, "leak.py"))
        self.add_file("keep.md", "# Kept\n")
        self.commit_all()

        # The entries really are tracked as symlinks (git mode 120000).
        self.assertEqual(self._tracked_mode("leak.md"), "120000")
        self.assertEqual(self._tracked_mode("leak.py"), "120000")

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        code_index = self.read("CODE_INDEX.md")

        for output in (repo_map, code_index):
            self.assertNotIn("OUTSIDE_HEADING_MARKER", output)
            self.assertNotIn("outside_def_marker", output)
            self.assertNotIn("leak.md", output)
            self.assertNotIn("leak.py", output)

        # The guard must not skip everything: a normal file still appears.
        self.assertIn("keep.md", repo_map)

    def test_symlink_inside_repo_is_excluded(self):
        self.add_file("real.md", "# Real file\n")
        os.symlink(
            os.path.join(self.repo, "real.md"),
            os.path.join(self.repo, "alias.md"),
        )
        self.commit_all()

        self.assertEqual(self._tracked_mode("alias.md"), "120000")

        self.generate()
        repo_map = self.read("REPO_MAP.md")
        code_index = self.read("CODE_INDEX.md")

        self.assertNotIn("alias.md", repo_map)
        self.assertNotIn("alias.md", code_index)
        # The real target is a normal tracked file (not a symlink) and stays.
        self.assertIn("real.md", repo_map)


if __name__ == "__main__":
    unittest.main()
