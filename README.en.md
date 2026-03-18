# Coding Agent Lab

Chinese is the primary entry: [README.md](./README.md)

This repository is for studying, breaking down, reproducing, and comparing different coding agents.

## What This Repo Covers

- Study product behavior, workflows, and implementation ideas of coding agents
- Organize public references, cleanroom analysis, and experiment notes
- Reproduce core agent workflows starting from small runnable prototypes

## Current Focus

- `Claude Code`
- `OpenCode`
- Common topics such as agent loops, tool use, context and memory, planning, and safety boundaries

## Directory Layout

- `docs/`: study notes, topic breakdowns, and comparisons
- `reproductions/`: reproduction projects and experiment code
- `notes/`: findings, questions, and working notes
- `assets/`: screenshots, diagrams, and other static assets
- `.agents/skills/`: local skills used inside this repository

## Quick Links

- [Claude Code topic](./docs/claude-code.md)
- [Overview](./docs/overview.md)
- [Comparisons](./docs/comparisons.md)
- [OpenCode](./docs/opencode.md)
- [Findings](./notes/findings.md)

## Usage

There is no single global build command for the whole repository yet. Use the commands documented inside each subproject when available.

Common inspection commands:

```bash
git status
rg "keyword" .
find reproductions -maxdepth 2 -type f | sort
```

## Goal

Build a solid base of research notes and minimal reproductions first, then expand toward tool use, context management, execution flow, and evaluation.
