#!/bin/sh
# Tests for scripts/export-repo.sh (etchwork extraction design §6 / task 3
# brief step 2). Prints PASS/FAIL per check; exits 1 if any FAIL. Never
# touches a real remote: the "remote" is a local bare repo under a scratch
# folder, removed on exit.
set -u
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
export_sh="$root/scripts/export-repo.sh"
etchwork="$root/etchwork"
scratch="${TMPDIR:-/tmp}/etchwork-export-test.$$"
mkdir -p "$scratch"
cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT

fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

# A copy of the same "git-visible files under etchwork/" set the export
# script copies, built independently (not by calling the script), so the
# diff in check 1 actually verifies the copy rather than checking the
# script against itself.
reference_tree() {
  # reference_tree <dest dir>
  dest="$1"
  (cd "$root" && git ls-files --cached --others --exclude-standard .) | while IFS= read -r f; do
    mkdir -p "$dest/$(dirname "$f")"
    cp -p "$root/$f" "$dest/$f"
  done
}

# --- 1: export to a fresh dir ---
export1="$scratch/export1"
out1=$("$export_sh" "$export1" 2>&1)
rc1=$?
if [ "$rc1" = 0 ]; then pass "fresh export: exits 0"; else fail "fresh export: exits 0" "exit $rc1: $out1"; fi

log_count=$(git -C "$export1" log --oneline 2>/dev/null | wc -l | tr -d ' ')
[ "$log_count" = 1 ] && pass "fresh export: exactly one commit" || fail "fresh export: exactly one commit" "count=$log_count"

want_name=$(git config --get user.name || true)
want_email=$(git config --get user.email || true)
got_author=$(git -C "$export1" log -1 --format='%an <%ae>' 2>/dev/null || true)
want_author="$want_name <$want_email>"
[ "$got_author" = "$want_author" ] && pass "fresh export: commit author is the configured identity" || fail "fresh export: commit author is the configured identity" "got=$got_author want=$want_author"

got_subject=$(git -C "$export1" log -1 --format='%B' 2>/dev/null | head -1)
case "$got_subject" in
  "Initial import from flai (FlologixAI/flai@"*) pass "fresh export: message starts with the provenance line" ;;
  *) fail "fresh export: message starts with the provenance line" "$got_subject" ;;
esac

notice_sha=$(grep -oE 'commit [0-9a-f]{7,40}' "$root/NOTICE" 2>/dev/null | head -1 | awk '{print $2}')
if [ "$got_subject" = "Initial import from flai (FlologixAI/flai@$notice_sha)" ]; then
  pass "fresh export: provenance SHA comes from NOTICE, not HEAD"
else
  fail "fresh export: provenance SHA comes from NOTICE, not HEAD" "subject=$got_subject notice_sha=$notice_sha"
fi

reference="$scratch/reference"
mkdir -p "$reference"
reference_tree "$reference"
diff_out=$(diff -r --exclude=.git "$reference" "$export1" 2>&1)
if [ -z "$diff_out" ]; then
  pass "fresh export: exported files equal the staged tree's git-visible files"
else
  fail "fresh export: exported files equal the staged tree's git-visible files" "$diff_out"
fi

# --- 2: a non-empty target is refused ---
nonempty="$scratch/nonempty"
mkdir -p "$nonempty"
echo x > "$nonempty/already-here"
out2=$("$export_sh" "$nonempty" 2>&1)
rc2=$?
[ "$rc2" = 1 ] && pass "non-empty target: exits 1" || fail "non-empty target: exits 1" "exit $rc2: $out2"

# --- 3: no configured identity ---
noid_home="$scratch/noid-home"; mkdir -p "$noid_home"
noid_cwd="$scratch/noid-cwd"; mkdir -p "$noid_cwd"
noid_target="$scratch/noid-target"
out3=$(cd "$noid_cwd" && HOME="$noid_home" GIT_CONFIG_NOSYSTEM=1 "$export_sh" "$noid_target" 2>&1)
rc3=$?
[ "$rc3" = 1 ] && pass "no identity: exits 1" || fail "no identity: exits 1" "exit $rc3: $out3"
case "$out3" in
  *"are not configured"*) pass "no identity: prints the configure message" ;;
  *) fail "no identity: prints the configure message" "$out3" ;;
esac
if [ -e "$noid_target" ]; then fail "no identity: creates nothing" "found $noid_target"; else pass "no identity: creates nothing"; fi

# --- 4: the remote path, pushed to a local bare repo ---
remote_bare="$scratch/remote.git"
git init -q --bare "$remote_bare"
export4="$scratch/export4"
out4=$("$export_sh" "$export4" "$remote_bare" 2>&1)
rc4=$?
[ "$rc4" = 0 ] && pass "remote export: exits 0" || fail "remote export: exits 0" "exit $rc4: $out4"
remote_log=$(git -C "$remote_bare" log --oneline main 2>&1)
remote_log_count=$(printf '%s\n' "$remote_log" | grep -c .)
[ "$remote_log_count" = 1 ] && pass "remote export: the bare repo has exactly one commit" || fail "remote export: the bare repo has exactly one commit" "$remote_log"
case "$remote_log" in
  *"Initial import from flai"*) pass "remote export: pushed commit carries the provenance message" ;;
  *) fail "remote export: pushed commit carries the provenance message" "$remote_log" ;;
esac

# --- 5: the exported repo works on its own ---
if "$export1/etchwork" help >/dev/null 2>&1; then
  pass "exported repo: ./etchwork help exits 0"
else
  fail "exported repo: ./etchwork help exits 0" "exit $?"
fi

staged_build_out=$("$etchwork" build 2>&1)
staged_image=$(printf '%s\n' "$staged_build_out" | tail -1)
case "$staged_image" in
  IMAGE=*)
    p="$scratch/p"
    mkdir -p "$p"
    exported_init_out=$("$export1/etchwork" init "$p" --client claude-code 2>&1)
    exported_image=$(printf '%s\n' "$exported_init_out" | tail -1)
    if [ "$exported_image" = "$staged_image" ]; then
      pass "exported repo: init prints the same IMAGE= as the staged tree"
    else
      fail "exported repo: init prints the same IMAGE= as the staged tree" "staged=$staged_image exported=$exported_image ($exported_init_out)"
    fi
    ;;
  *)
    fail "exported repo: init prints the same IMAGE= as the staged tree" "staged tree build gave no IMAGE=: $staged_build_out"
    ;;
esac

# --- 6: a NOTICE with no commit SHA is refused, and nothing is created ---
badnotice_root="$scratch/badnotice"
mkdir -p "$badnotice_root/scripts"
cp "$export_sh" "$badnotice_root/scripts/export-repo.sh"
echo "no provenance line here" > "$badnotice_root/NOTICE"
badnotice_target="$scratch/badnotice-target"
out6=$("$badnotice_root/scripts/export-repo.sh" "$badnotice_target" 2>&1)
rc6=$?
[ "$rc6" = 1 ] && pass "NOTICE without a commit SHA: exits 1" || fail "NOTICE without a commit SHA: exits 1" "exit $rc6: $out6"
if [ -e "$badnotice_target" ]; then
  fail "NOTICE without a commit SHA: creates nothing" "found $badnotice_target"
else
  pass "NOTICE without a commit SHA: creates nothing"
fi

echo "$fails failures"
[ "$fails" -eq 0 ]
