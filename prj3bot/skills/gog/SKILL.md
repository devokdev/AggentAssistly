---
name: gog
description: Use Google Workspace through prj3bot gog commands (Docs, Sheets, Drive, Classroom, Slides), including Telegram prefixes like "prj3bot doc ...".
---

# GOG Skill

Use this skill when users ask to create/read/list Google Workspace resources.

## Command Entry Points

- CLI one-shot: `prj3bot gog -m "<command>"`
- CLI interactive: `prj3bot gog`
- Telegram prefixes:
  - `prj3bot doc ...`
  - `prj3bot sheet ...`
  - `prj3bot drive ...`
  - `prj3bot classroom ...`
  - `prj3bot slides ...`
  - `prj3bot gog ...`

## Fast Patterns

- Create doc from instruction:
  - `doc do create a 200 words article on transformers and give me the link`
- Read docs:
  - `doc list 5`
  - `doc read 1`
- Sheets:
  - `sheet do create a weekly study tracker with columns date, topic, status`
  - `sheet read 1 A1:Z20`
  - `sheet append 1 | 2026-03-05 | Transformers | done`
- Drive:
  - `drive list 10`
  - `drive search transformer`
  - `drive show 1`
- Slides:
  - `slides do create a concise deck on transformers for beginners`
  - `slides list 5`
  - `slides read 1`
- Classroom:
  - `classroom courses 10`
  - `classroom coursework 1 10`
  - `classroom announcements 1 10`

## Notes

- Google OAuth credentials are configured in `~/.prj3bot/config.json` under `googleWorkspace`.
- If credentials are missing, return a clear setup message and stop.
- Prefer list->index workflows to reduce user typing.
