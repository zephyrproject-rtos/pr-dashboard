#!/usr/bin/env python3

# Copyright 2024 Google LLC
# Copyright (c) 2024 The Linux Foundation
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os
import sys
import github

token = os.environ["GITHUB_TOKEN"]

DATA_FILE = "cache/data_dump.json"

PAGE_SIZE_SMALL_REPOS = 10
PAGE_SIZE_ZEPHYR = 30


def print_rate_limit(gh, org):
    rate_limit = gh.get_rate_limit().resources.graphql
    print(f"GraphQL rate limit for {org}: {rate_limit.remaining}/{rate_limit.limit}")


def repo_from_url(url):
    return url.split("/")[-3]


def save_prs(data):
    if not os.path.exists("cache"):
        os.mkdir("cache")

    with open(DATA_FILE, "w") as outfile:
        json.dump(data, outfile, indent=4)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "-o", "--org", default="zephyrproject-rtos", help="Github organisation"
    )
    parser.add_argument(
        "-m", "--manifest-repo", default="zephyr", help="Manifest repo"
    )
    parser.add_argument(
        "-d", "--doc-url",
        default="https://builds.zephyrproject.io/zephyr/pr/${pr}/docs/index.html",
        help="Doc URL, use ${pr} for the PR number"
    )

    parser.add_argument(
        "-r",
        "--repos",
        default="zephyr,segger",
        help="Github repositories, comma separated",
    )

    return parser.parse_args(argv)


GRAPHQL_QUERY = """
query (
  $org: String!
  $repo: String!
  $prCursor: String
  $prPageSize: Int!
  $commentsCursor: String
  $reviewsCursor: String
  $dismissedReviewsTimelineItemsCursor: String
) {
  rateLimit {
    cost
    limit
    remaining
    used
    resetAt
  }
  repository(owner: $org, name: $repo) {
    pullRequests(first: $prPageSize, after: $prCursor, states: OPEN) {
      totalCount
      edges {
        node {
          number
          url
          title
          isDraft
          repository {
            name
          }
          baseRefName
          createdAt
          updatedAt
          mergeable
          author {
            login
          }
          assignees(first: 10) {
            edges {
              node {
                login
              }
            }
          }
          reviewRequests(last: 30) {
            nodes {
              requestedReviewer {
                ... on User {
                  login
                }
              }
            }
          }
          latestOpinionatedReviews(first: 50, after: $reviewsCursor) {
            nodes {
              author {
                login
              }
              state
              submittedAt
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
          dismissedReviewsTimelineItems: timelineItems(
            first: 20
            itemTypes: [REVIEW_DISMISSED_EVENT]
            after: $dismissedReviewsTimelineItemsCursor
          ) {
            nodes {
              __typename
              ... on ReviewDismissedEvent {
                actor {
                  login
                }
                review {
                  author {
                    login
                  }
                }
                createdAt
                previousReviewState
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
          comments(first: 80, after: $commentsCursor) {
            nodes {
              author {
                login
              }
              createdAt
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
          commits(last: 1) {
            nodes {
              commit {
                statusCheckRollup {
                  state
                }
              }
            }
          }
          labels(first: 20) {
            nodes {
              name
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""


def fetch_paginated_data(gh, query, variables, pr, data_key):
    """
    Generic function to handle pagination for GraphQL queries.

    Parameters:
        gh (object): GitHub client object.
        query (str): GraphQL query string.
        variables (dict): Variables for the GraphQL query.
        pr (dict): Dictionary containing pull request data.
        data_key (str): Key to identify the data in the `pr` dictionary.

    Returns:
        None: Updates the `pr` dictionary in place.
    """
    page_info = pr[data_key]["pageInfo"]
    while page_info["hasNextPage"]:
        cursor_key = f"{data_key}Cursor"
        variables[cursor_key] = page_info["endCursor"]
        _, result = gh.requester.graphql_query(query, variables)
        pr_edges = result["data"]["repository"]["pullRequests"]["edges"]
        pr_nodes = [edge["node"] for edge in pr_edges]
        pr_node = next((node for node in pr_nodes if node["number"] == pr["number"]), None)
        pr[data_key]["nodes"].extend(pr_node[data_key]["nodes"])
        page_info = pr_node[data_key]["pageInfo"]


def fetch_pull_requests(gh, query, variables):
    data = []
    cursor = None
    while True:
        variables["prCursor"] = cursor
        _, result = gh.requester.graphql_query(query, variables)
        repository = result["data"]["repository"]
        pull_requests = repository["pullRequests"]["edges"]

        for pr in pull_requests:
            pr = pr["node"]
            fetch_paginated_data(gh, query, variables, pr, "comments")
            fetch_paginated_data(gh, query, variables, pr, "latestOpinionatedReviews")
            fetch_paginated_data(gh, query, variables, pr, "dismissedReviewsTimelineItems")

        data.extend(pull_requests)

        page_info = repository["pullRequests"]["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return data


def main(argv):
    args = parse_args(argv)
    all_prs = []

    auth = github.Auth.Token(os.environ.get("GITHUB_TOKEN", None))
    gh = github.Github(auth=auth)
    print_rate_limit(gh, args.org)

    for repo in args.repos.split(","):
        print(f"Processing PRs for {args.org}/{repo}")
        # Optimize page size for repos known to have few PRs so that GraphQL query cost is minimized
        pr_page_size = (
            PAGE_SIZE_ZEPHYR
            if (args.org == "zephyrproject-rtos" and repo == "zephyr")
            else PAGE_SIZE_SMALL_REPOS
        )

        all_prs.extend(
            fetch_pull_requests(
                gh,
                GRAPHQL_QUERY,
                {"org": args.org, "repo": repo, "prPageSize": pr_page_size},
            )
        )

    print_rate_limit(gh, args.org)
    metadata = {"org": args.org, "manifest_repo": args.manifest_repo,
                "doc_url": args.doc_url}
    all_prs = {"metadata": metadata, "prs": [pr["node"] for pr in all_prs]}
    save_prs(all_prs)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
