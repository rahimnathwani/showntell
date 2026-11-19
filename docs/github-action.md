# The GitHub Action

`rahimnathwani/showntell` is also a GitHub Action. It voices a walkthrough's Markdown on the runner with the open Kokoro model (ONNX on Linux), so no API key or hosted service is involved, and attaches the result to the run. On a pull request it posts one comment with the outline, the transcript and a download link, and updates that comment on each push.

```yaml
name: Walkthrough
on:
  pull_request:
    paths: [".showntell/**"]

permissions:
  contents: read
  pull-requests: write   # for the comment

jobs:
  walkthrough:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # `diff ... rev=` blocks read commits
      - uses: rahimnathwani/showntell@v0.2.7
        with:
          markdown: .showntell/pr.md
```

| Input | Default | What |
|---|---|---|
| **markdown** | (required) | the walkthrough's Markdown, relative to the repository root; `file=` and `rev=` read from the checkout |
| **format** | `html` | `html`: one page that plays offline; `mp4`: a 1080p video (installs Chromium and FFmpeg, about a minute more); `both` |
| **name** | `walkthrough` | the artifact's name; one comment per name |
| **comment** | `true` | post or update the pull request comment |
| **token** | `github.token` | token for the comment |

Outputs: `folder` (holding `walkthrough.html`, `walkthrough.mp4` and `summary.md`) and `artifact-url`.

## Where the Markdown comes from

- **Written with the change:** ask your agent to write the walkthrough to `.showntell/pr.md` in the [format](format.md) and commit it with the change. The `diff file=... rev=...` blocks keep it tied to the code: reviewers hear about the lines they are reviewing, read from the commit.
- **Written in CI:** any earlier step that writes the file works, such as an agent step with its own credentials. The action itself only voices and publishes.

## Good to know

- **Time:** installing and voicing a three-minute walkthrough takes a few minutes on a standard runner; the voice model (about 110 MB) is cached between runs.
- **Viewing:** artifacts download as a zip; unzip and open `walkthrough.html`. The transcript in the comment can be read without downloading anything.
- **Forks:** pull requests from forks get a read-only token, so the comment step fails there; run with `comment: "false"` or from `pull_request_target` with care.
- **Versions:** the action installs the dependency versions in its release's lockfile.
