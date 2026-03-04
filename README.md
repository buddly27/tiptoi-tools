# tiptoi-tools

Python CLI for inspecting, extracting, and building Ravensburger Tiptoi GME files.

## Installation

```bash
pip install tiptoi-tools
```

## Usage

```bash
# Show file information
tiptoi-tools file.gme info

# Export to YAML and media files
tiptoi-tools file.gme export

# Build GME from YAML
tiptoi-tools file.yaml build

# Play audio by OID or media index
tiptoi-tools file.gme play 123
tiptoi-tools file.gme play @456
```

## Commands

| Command | Description |
|---------|-------------|
| `info` | Show GME file metadata and structure |
| `export` | Export to YAML with media files |
| `build` | Build GME from tttool-compatible YAML |
| `play` | Play audio by OID or media index |
| `scripts` | Browse and search scripts |
| `games` | List games and their structure |
| `oids` | Explore OID relationships |

## Acknowledgments

This project is inspired by and compatible with [tttool](https://github.com/entropia/tip-toi-reveng), the original Tiptoi reverse-engineering toolkit created by Joachim Breitner ([@nomeata](https://github.com/nomeata)). The GME file format documentation and YAML schema originate from the tttool project.

## License

MIT
