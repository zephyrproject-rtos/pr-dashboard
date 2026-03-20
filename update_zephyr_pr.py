#!/usr/bin/env python3

# Copyright 2024 Google LLC
# Copyright (c) 2024 The Linux Foundation
# SPDX-License-Identifier: Apache-2.0

from west.manifest import Manifest
from west.manifest import ManifestProject
import subprocess

manifest = Manifest.from_file()


def repo_name(project):
    url = project.url.rstrip("/")
    repo = url.rsplit("/", 1)[-1]
    return repo.removesuffix(".git") or project.name

repos = [
        "action-manifest",
        "action-zephyr-setup",
        "docker-image",
        "example-application",
        "infrastructure",
        "pr-dashboard",
        "sdk-ng",
        "zephyr",
]

for project in manifest.get_projects([]):
    if not manifest.is_active(project):
        continue

    if isinstance(project, ManifestProject):
        continue

    repos.append(repo_name(project))

repos_arg = ",".join(repos)

subprocess.run(["python", "-u", "update_pr.py", "--repos", repos_arg])
