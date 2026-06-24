#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — regenerate the README/docs demo (demo.gif + demo.png)
#
# Deterministic, self-contained: starts a throwaway hub, declares a plan with a
# dependency, completes a task, and shows the dependent unblock — no model worker.
# Requires `synapse` (this package) plus `asciinema` and `agg` on PATH:
#   pipx install asciinema
#   download `agg` from https://github.com/asciinema/agg/releases
# Then, from the repository root:
#   bash docs/assets/record_demo.sh
# It writes docs/assets/demo.cast, docs/assets/demo.gif, and docs/assets/demo.png.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
port=8898
uri="ws://localhost:${port}"
db="${work}/hub.db"

# The recorded session, written as a script so asciinema can run it in a pty.
cat > "${work}/session.sh" <<SESSION
#!/usr/bin/env bash
set -u
uri="${uri}"
H='\033[38;5;81m'; P='\033[38;5;245m'; D='\033[1;32m'; R='\033[0m'
prompt() { printf "\${H}synapse\${R} \${P}~\${R} \${D}\\\$\${R} %s\n" "\$*"; }
run() { prompt "\$*"; sleep 0.7; eval "synapse \${*#synapse } --uri \$uri" 2>&1 | sed 's/^/  /'; sleep 1.4; }

prompt "synapse hub --port ${port} --db ./hub.db   # crash-durable coordination bus"
sleep 0.6
synapse hub --port ${port} --db "${db}" >/dev/null 2>&1 &
hubpid=\$!
for _ in \$(seq 1 40); do synapse health --uri "\$uri" >/dev/null 2>&1 && break; sleep 0.1; done
printf "  \${P}hub listening on %s\${R}\n" "\$uri"; sleep 1.4

run "synapse task declare BUILD --title 'compile the core'"
run "synapse task declare TEST --title 'run the suite' --depends-on BUILD"
run "synapse board"
printf "  \${P}# TEST is blocked on BUILD — only BUILD is ready\${R}\n"; sleep 1.8

run "synapse task update BUILD --status done"
run "synapse board"
printf "  \${P}# BUILD done -> TEST is unblocked and ready\${R}\n"; sleep 2.2

kill \$hubpid 2>/dev/null
printf "\n\${D}local-first · one dependency · crash-durable\${R}\n"; sleep 1.2
SESSION

asciinema rec --overwrite -c "bash ${work}/session.sh" "${here}/demo.cast"
agg --cols 86 --rows 27 --font-size 16 --speed 1.15 --idle-time-limit 2 \
    "${here}/demo.cast" "${here}/demo.gif"

# A static still (the final frame) for contexts where an animation is too heavy.
if command -v ffmpeg >/dev/null 2>&1; then
  frames="${work}/frames"; mkdir -p "$frames"
  ffmpeg -y -i "${here}/demo.gif" "${frames}/f_%04d.png" >/dev/null 2>&1
  cp "$(ls "${frames}"/f_*.png | tail -1)" "${here}/demo.png"
fi

echo "wrote ${here}/demo.gif$( [ -f "${here}/demo.png" ] && echo ' + demo.png' )"
