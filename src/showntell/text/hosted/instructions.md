showntell turns Markdown into narrated slide walkthroughs, voiced on this server and played in the browser.

Use it when watching and listening would teach faster than reading: code walkthroughs, diffs and pull requests, concepts, architecture and data flow, maths.

Flow: start_walkthrough with `opening`, the title slide and one or two spoken sentences → share the link → add_slides, one to three slides per call → finish_walkthrough, whose reply lists render and layout problems found by drawing every slide → revise_slide what it names. Call preview_slides only for a slide it names, a drawing or diagram you want to see, or when the user asks.

This server is shared by a company and sees nothing on your machine or its own disk. Read the code yourself, then put it in the Markdown: code in fenced blocks with `start=` for the first line number, changes as unified `diff` blocks or `old`/`new` pairs. `project_dir`, `file=`, `rev=` and images are refused. Drawings (```svg), diagrams, charts and tables all work.

The reply that carries the opening says when the link is playable: the start_walkthrough reply, or the first add_slides reply if you started without `opening`. Send that link to the user at once, in a message, before your next tool call; they watch while you write the rest. That reply also carries the full authoring guide; read it before writing the body.

Walkthroughs belong to the user who made them; nobody else can open the link until the owner shares it with share_walkthrough, to named colleagues or the whole company.

To change a walkthrough later, find its handle with list_walkthroughs, read its Markdown from the resource showntell://walkthroughs/{id}, and replace slides with revise_slide.
