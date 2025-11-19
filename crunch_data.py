#!/usr/bin/env python3

# Copyright 2024 Google LLC
# Copyright (c) 2024 The Linux Foundation
# SPDX-License-Identifier: Apache-2.0

from collections import defaultdict
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone, timedelta
from typing import Optional
import statistics
import json
import os
import sys

OUTDIR = "public"
INFILE = "cache/data_dump.json"

# --- SCORING CONFIGURATION ---
ENGAGEMENT_SPEED_TARGET = int(timedelta(days=2).total_seconds())
ENGAGEMENT_SPEED_WORST = int(timedelta(days=30).total_seconds())

# Relative importance of Reliability score (number of PRs engaged vs assigned)
# in the final Engagement Score.
WEIGHT_RELIABILITY = 0.5
# Relative importance of Speed score in the final Engagement Score.
WEIGHT_SPEED = 0.5
# -----------------------------

@dataclass
class User:
    # set of PR keys where the user is the author
    author: set = field(default_factory=set)
    # set of PR keys where the user is one of assignees
    assignee: set = field(default_factory=set)
    # set of PR keys where the user is requesting changes
    blocking: set = field(default_factory=set)
    # set of PR keys where the user has approved
    approved: set = field(default_factory=set)
    # set of PR keys where the user has previously approved
    previously_approved: set = field(default_factory=set)
    # set of PR keys where the user is one of the reviewers
    reviewer: set = field(default_factory=set)
    # set of PR keys where the user has commented
    commented: set = field(default_factory=set)
    # map of PR key to timestamp of last action by the user
    last_action: dict = field(default_factory=dict)
    # list of engagement delays (in seconds) for the PRs the user was assigned to
    _engaged_delays_seconds: list = field(default_factory=list)

    reliability_score: float = 0.0
    speed_score: float = 0.0
    engagement_score: Optional[float] = None

    def toJSON(self):
        out = {}
        for f in fields(self):
            if not f.name.startswith('_'):
                out[f.name] = getattr(self, f.name)
        return out


@dataclass
class PR:
    title: str = None
    author: str = None
    base: str = None
    assignee_names: set = field(default_factory=set)
    reviewer_names: set = field(default_factory=set)
    assignee_approved: int = 0
    approved: int = 0
    blocked: int = 0
    created_at: str = None
    updated_at: str = None
    draft: bool = False
    mergeable: bool = True
    unknown_mergeable_status: bool = False
    needs_rebase: bool = False
    ci_passes: bool = True
    ci_pending: bool = True
    trivial: bool = False
    # Dictionary of assignee name to time (in seconds) taken to first engage after assignment
    time_to_engage_after_assignment: dict = field(default_factory=dict)

    def toJSON(self):
        out = {}
        for f in fields(self):
            out[f.name] = getattr(self, f.name)
        return out


class Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        elif isinstance(obj, User):
            return obj.toJSON()
        elif isinstance(obj, PR):
            return obj.toJSON()
        return json.JSONEncoder.default(self, obj)


def seconds_excluding_weekends(start: datetime, end: datetime) -> float:
    """
    Return the number of seconds between start and end,
    ignoring any time that falls on Saturday or Sunday (GMT/UTC).
    """
    if end <= start:
        return 0.0

    current = start
    total_seconds = 0.0

    while current < end:
        # If we're on a weekend, jump to next Monday 00:00 UTC.
        if current.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
            days_to_monday = 7 - current.weekday()
            next_monday = datetime(
                current.year, current.month, current.day, tzinfo=current.tzinfo
            ) + timedelta(days=days_to_monday)
            current = max(current, next_monday)
            continue

        # We're on a weekday: consume up to either end of this day or `end`,
        # whichever comes first.
        next_day = datetime(
            current.year, current.month, current.day, tzinfo=current.tzinfo
        ) + timedelta(days=1)

        segment_end = min(end, next_day)
        total_seconds += (segment_end - current).total_seconds()
        current = segment_end

    return total_seconds


def main(argv):
    users = defaultdict(User)
    prs = {}

    with open(INFILE, "r") as infile:
        json_dump = json.load(infile)

    metadata = json_dump["metadata"]
    pr_dump = json_dump["prs"]

    print(metadata)

    for pr_data in pr_dump:
        key = f"{pr_data['repository']['name']}/{pr_data['number']}"
        print(f"processing: {key}")
        pr = PR()
        pr.title = pr_data["title"]
        pr.author = pr_data["author"]["login"]
        pr.base = pr_data["baseRefName"]
        pr.created_at = pr_data["createdAt"]
        pr.updated_at = pr_data["updatedAt"]
        pr.draft = pr_data["isDraft"]

        pr.mergeable = pr_data["mergeable"] == "MERGEABLE"
        pr.needs_rebase = pr_data["mergeable"] == "CONFLICTING"
        pr.unknown_mergeable_status = pr_data["mergeable"] == "UNKNOWN"

        commits_nodes = pr_data["commits"]["nodes"]
        if commits_nodes:
            status_check = commits_nodes[0]["commit"]["statusCheckRollup"]
            pr.ci_passes = status_check and status_check.get("state") == "SUCCESS"
            pr.ci_pending = status_check and status_check.get("state") == "PENDING"

        # Check for "Trivial" label
        labels = pr_data.get("labels", {}).get("nodes", [])
        pr.trivial = any(label["name"] == "Trivial" for label in labels)

        assignment_times = {}
        # Find the latest assignment time for each assignee
        for item in pr_data.get("assignmentTimelineItems", {}).get("nodes", []):
            if (assignee := item.get("assignee")) and (login := assignee.get("login")):
                assignment_times[login] = max(
                    assignment_times.get(login, ""), item["createdAt"]
                )

        actions = defaultdict(list)
        sources = [
            ("comments", "createdAt"),
            ("reviews", "submittedAt"),
            ("latestOpinionatedReviews", "submittedAt"),
        ]

        for source_key, time_field in sources:
            for item in pr_data.get(source_key, {}).get("nodes", []):
                author = item.get("author")
                ts = item.get(time_field)
                if author and ts:
                    actions[author["login"]].append(datetime.fromisoformat(ts))

        for edge in pr_data["assignees"]["edges"]:
            user = edge["node"]["login"]

            # Get string timestamp, handle Z, convert to datetime
            start_str = assignment_times.get(user, pr.created_at).replace("Z", "+00:00")
            assigned_at = datetime.fromisoformat(start_str)

            user_acts = sorted(actions[user])
            # Find first action that happened after assignment
            first_act = next((t for t in user_acts if t > assigned_at), None)

            if first_act:
                delay = seconds_excluding_weekends(assigned_at, first_act)
                users[user]._engaged_delays_seconds.append(delay)
                pr.time_to_engage_after_assignment[user] = delay
            else:
                pr.time_to_engage_after_assignment[user] = None

        users[pr.author].author.add(key)

        for assignee in pr_data["assignees"]["edges"]:
            assignee_name = assignee["node"]["login"]
            users[assignee_name].assignee.add(key)
            pr.assignee_names.add(assignee_name)

        for reviewer in pr_data["reviewRequests"]["nodes"]:
            if not reviewer["requestedReviewer"]:
                print("skipping review, no data")
                continue
            reviewer_name = reviewer["requestedReviewer"]["login"]
            users[reviewer_name].reviewer.add(key)
            pr.reviewer_names.add(reviewer_name)

        approved = defaultdict(str)
        changes_requested = defaultdict(str)
        for review in pr_data["latestOpinionatedReviews"]["nodes"]:
            state = review["state"]
            if review["author"] is None:
                continue
            reviewer_name = review["author"]["login"] if review["author"] else "ghost"
            print(f"review: {reviewer_name} {state}")
            match state:
                case "COMMENTED":
                    users[reviewer_name].commented.add(key)
                    pr.reviewer_names.add(reviewer_name)
                case "APPROVED":
                    approved[reviewer_name] = review
                case "CHANGES_REQUESTED":
                    changes_requested[reviewer_name] = review
                case "PENDING":
                    # ignore pending reviews from the user associated to the GitHub token being used
                    pass
            users[reviewer_name].last_action[key] = review["submittedAt"]

        for comment in pr_data["comments"]["nodes"]:
            if comment["author"] is None:
                continue
            commenter_name = comment["author"]["login"]
            # TODO: it might make sense to populate the commented set with the PRs where the user
            # *only* commented, not when they also actually reviewed.
            users[commenter_name].commented.add(key)

        # look for dismissed reviews where author had previously approved, meaning they may be
        # interested in refreshing their +1
        for tl_item in pr_data["dismissedReviewsTimelineItems"]["nodes"]:
            review = tl_item["review"]
            reviewer_name = review["author"]["login"] if review["author"] else "ghost"
            prev_review_state = tl_item["previousReviewState"]
            if prev_review_state != "APPROVED":
                continue
            if tl_item["actor"] and tl_item["actor"]["login"] == reviewer_name:
                # ignore self-dismissed reviews
                continue
            if reviewer_name not in approved and reviewer_name not in changes_requested:
                print(f"PR {key} {reviewer_name} previously approved and could do a refresh +1")
                users[reviewer_name].previously_approved.add(key)

        for reviewer_name, review in approved.items():
            users[reviewer_name].approved.add(key)
            if reviewer_name in pr.assignee_names:
                pr.assignee_approved += 1
                pr.assignee_names.remove(reviewer_name)
                pr.assignee_names.add(f"+{reviewer_name}")
            else:
                pr.approved += 1
                pr.reviewer_names.add(f"+{reviewer_name}")

        for reviewer_name, review in changes_requested.items():
            users[reviewer_name].blocking.add(key)
            if reviewer_name in pr.assignee_names:
                pr.assignee_names.remove(reviewer_name)
                pr.assignee_names.add(f"-{reviewer_name}")
            else:
                pr.reviewer_names.add(f"-{reviewer_name}")
            pr.blocked += 1

        prs[key] = pr

    for user, data in users.items():
        print(f"{user} {data}")

        total_assigned = len(data.assignee)
        engaged_count = len(data._engaged_delays_seconds)

        print(f"  total assigned: {total_assigned} engaged: {engaged_count}")

        # 1. Reliability Score: Do they engage?
        if total_assigned > 0:
            # Percentage of assigned PRs where they actually acted
            data.reliability_score = (engaged_count / total_assigned) * 100.0
        else:
            # If never assigned, reliability is technically N/A.
            # Setting to 0 or 100 depends on philosophy.
            # Setting to 0 ensures we don't feature inactive users.
            data.reliability_score = 0.0

        # 2. Speed Score: How fast are they (when they do engage)?
        if engaged_count > 0:
            metric_delay = statistics.median(data._engaged_delays_seconds)

            if metric_delay <= ENGAGEMENT_SPEED_TARGET:
                data.speed_score = 100.0
            elif metric_delay >= ENGAGEMENT_SPEED_WORST:
                data.speed_score = 0.0
            else:
                # Linear Interpolation (Sliding Scale)
                # As delay increases from Target -> Limit, Score decreases 100 -> 0
                total_range = ENGAGEMENT_SPEED_WORST - ENGAGEMENT_SPEED_TARGET
                excess_delay = metric_delay - ENGAGEMENT_SPEED_TARGET
                penalty_percent = (excess_delay / total_range) * 100.0
                data.speed_score = 100.0 - penalty_percent
        else:
            data.speed_score = 0.0

        # 3. Final Weighted Score
        if total_assigned > 0 or engaged_count > 0:
            final = (WEIGHT_RELIABILITY * data.reliability_score) + (
                WEIGHT_SPEED * data.speed_score
            )
            data.engagement_score = round(final, 1)
        else:
            # User has no activity and no assignments
            data.engagement_score = None

    print("")
    for pr, data in prs.items():
        print(f"PR {pr} {data}")

    if not os.path.exists(OUTDIR):
        os.mkdir(OUTDIR)

    with open(f"{OUTDIR}/metadata.json", "w") as outfile:
        json.dump(metadata, outfile, cls=Encoder, indent=4)

    with open(f"{OUTDIR}/users.json", "w") as outfile:
        json.dump(users, outfile, cls=Encoder, indent=4)

    with open(f"{OUTDIR}/prs.json", "w") as outfile:
        json.dump(prs, outfile, cls=Encoder, indent=4)

    print("\nEngagement Scores:")
    headers = ["User", "Engagement Score", "Assigned PRs"]
    rows = []
    for user, data in users.items():
        if data.engagement_score is not None:
            rows.append((user, data.engagement_score, len(data.assignee)))

    # Sort by engagement score descending
    rows.sort(key=lambda x: x[1], reverse=True)

    if rows:
        # Calculate column widths
        col_widths = [len(h) for h in headers]
        for row in rows:
            col_widths[0] = max(col_widths[0], len(str(row[0])))
            col_widths[1] = max(col_widths[1], len(f"{row[1]:.1f}"))
            col_widths[2] = max(col_widths[2], len(str(row[2])))

        # Create format string
        fmt = "| " + " | ".join(f"{{:<{w}}}" for w in col_widths) + " |"
        separator = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"

        print(separator)
        print(fmt.format(*headers))
        print(separator)
        for row in rows:
            print(fmt.format(row[0], f"{row[1]:.1f}", row[2]))
        print(separator)



if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
