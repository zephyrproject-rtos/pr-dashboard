# PR Dashboard

This set of scripts produces a static HTML page with an overview of the pull
requests involving a specific user, fetching data from multiple repositories
under a GitHub organization. It's been developed for the Zephyr project
specifically but it should be usable for any GitHub project.

## Script architecture

This is meant to be called periodically from a GitHub workflow and the output
served using GitHub pages. The basic workflow run as following:

- `update_zephyr_pr.py`: produces a list of repositories based on the west
  manifest and calls `update_pr.py` with the list. This step is optional, one
  could call `update_pr.py` with a static list of repositories directly.
- `update_pr.py`: fetches the pull request data using the GitHub APIs and dump
  the raw content into a big cache file that can be used by other scripts to
  read the data without having to query the GitHub APIs again.
- `crunch_data.py`: reads the raw data and produces a new set of files that are
  suitable for the dashboard UI to be used directly.

See the `.github/workflows/update.yaml` file for more details.

## Development and troubleshooting

Each of the scripts can run independently.

- `update_pr.py` can be called directly and only needs a `GITHUB_TOKEN`
  environment variable to be setup with a valid API token. By default it only
fetches a couple of fixed repositories.
- `crunch_data.py` can be run using the cached data from the latest data set
  already served on GitHub, these can be fetched using the
`download_cache_data` script:

```console
~/pr-dashboard$ mkdir cache
~/pr-dashboard$ ./download_cache_data 
...
2025-01-07 12:12:52 (18.0 MB/s) - ‘cache/data_dump.json.bz2’ saved [96004/96004]

~/pr-dashboard$ ls cache/
data_dump.json
~/pr-dashboard$ ./crunch_data.py
...
~/pr-dashboard$ ls public/
index.html  prs.json  style.css  users.json
~/pr-dashboard$ cd public/
~/pr-dashboard/public$ python -m http.server
Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...
```
