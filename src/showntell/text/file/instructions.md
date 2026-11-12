showntell turns Markdown into narrated slide walkthroughs, voiced locally and sent to the user as a video file.

Use it when watching and listening would teach faster than reading: code walkthroughs, diffs and pull requests, concepts, architecture and data flow, maths.

The user cannot open links on this machine: you run in a sandbox, or reach them through a chat app. Never send them a showntell link; send the finished file.

Flow: start_walkthrough with `opening`, the title slide and one or two spoken sentences → add_slides, one to three slides per call, until the walkthrough is complete → finish_walkthrough, whose reply lists render and layout problems found by drawing every slide → revise_slide what it names → export_walkthrough → send the user the MP4 it saves, attached through the channel you talk to them on. Call preview_slides only for a slide a reply names, a drawing or diagram you want to see, or when the user asks.

Pass `project_dir`, the project's absolute path, to start_walkthrough; a client that does not share its project folder otherwise has code read from the server's working directory.

The reply that carries the opening confirms the voice works and carries the full authoring guide; read it before writing the body. That is the start_walkthrough reply, or the first add_slides reply if you started without `opening`. Nothing can be watched before export, so there is no rush: write the whole walkthrough, then deliver it.

Pick the frame by content: the user likely watches on a phone. portrait fills the screen but fits only short code lines; square is the middle ground; landscape suits wide code and side-by-side diffs.

export_walkthrough keeps an MP4 within 16 MB by default, WhatsApp's video limit; pass max_mb for other channels: Telegram bots 50, Signal 100, email often 25. If it cannot fit, split the walkthrough into parts or raise the limit. Rendering takes about a tenth of the video's length plus a few seconds. format html saves one page that plays offline in a browser, for a user who would rather open it on a computer.

To change a walkthrough later, find its handle with list_walkthroughs, read its Markdown from the resource showntell://walkthroughs/{id}, replace slides with revise_slide, insert them with add_slides `after`, or move and remove them with arrange_slides, then export again and send the new file.
