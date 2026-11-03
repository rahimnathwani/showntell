showntell turns Markdown into narrated slide walkthroughs, voiced locally and played in the browser.

Use it when watching and listening would teach faster than reading: code walkthroughs, diffs and pull requests, concepts, architecture and data flow, maths.

Start first: call start_walkthrough with `opening` before reading code or researching; the opening plays while you work. Write it to hold without research: the question, the goal or why it matters, and any fact the request gives; what you have yet to read waits for later slides.

Flow: start_walkthrough with `opening`, the title slide and one or two spoken sentences → share the link → add_slides: first one short slide, from what the request gives if the rest needs reading; then one to three slides per call, reading for the next between calls → finish_walkthrough, whose reply lists render and layout problems found by drawing every slide → revise_slide what it names. Call preview_slides only for a slide it names, a drawing or diagram you want to see, or when the user asks.

Pass `project_dir`, the project's absolute path, to start_walkthrough; a client that does not share its project folder otherwise has code read from the server's working directory.

The reply that carries the opening says when the link is playable: the start_walkthrough reply, or the first add_slides reply if you started without `opening`. Send that link to the user at once, in a message, before your next tool call; they watch while you write the rest. That reply also carries the full authoring guide; read it before writing the body.

When the user wants a video, or a file to share, send or attach, call export_walkthrough after finish_walkthrough and send them the file it saves.

To change a walkthrough later, find its handle with list_walkthroughs, read its Markdown from the resource showntell://walkthroughs/{id}, and replace slides with revise_slide.
