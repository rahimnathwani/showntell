# How this action works
> Voice a walkthrough on the runner, then post it to the pull request

This action turns a Markdown walkthrough into a narrated page, without sending anything to a service.

## Voice it once {#voice .side}
> One folder export voices the Markdown; the page and the video reuse it

```yaml file=action.yml lines=76-84
```

[The folder export](#L78 "showntell export") voices every paragraph on the runner, with the open voice model cached between runs.

The page and [the video](#L81 "walkthrough.mp4") are drawn from that same walkthrough, so nothing is voiced twice.

## One comment per pull request {#comment}
> Each push updates the same comment

```yaml file=action.yml lines=109-120
```

The comment starts with [a hidden marker](#L109 "marker="). On the next push, the action [finds that comment](#L115 "existing=") and [edits it](#L117 "PATCH"), so the thread doesn't fill with copies.
