package main

import (
	"bytes"
	"io"
)

// shiftEnterReader wraps an io.Reader and translates terminal-emitted
// "Shift+Enter" sequences into the universal "Alt+Enter" sequence (ESC+CR)
// that Bubble Tea understands. This lets the chat handler treat Shift+Enter
// as a newline without requiring the user to touch their terminal config.
//
// Sequences translated:
//   - xterm modify-other-keys level 1: ESC [ 27 ; 2 ; 13 ~     (10 bytes)
//   - Kitty / CSI-u:                    ESC [ 13 ; 2 u           ( 7 bytes)
//
// Both are converted to ESC + CR (2 bytes), which Bubble Tea parses as
// Alt+Enter. The model already handles Alt+Enter as "insert newline."
//
// Partial sequences at buffer boundaries are held back and re-checked when
// more bytes arrive. Anything we don't recognise passes through untouched.
type shiftEnterReader struct {
	src  io.Reader
	hold []byte // bytes carried over from previous Read that may complete a sequence
}

func newShiftEnterReader(r io.Reader) *shiftEnterReader {
	return &shiftEnterReader{src: r}
}

var (
	modKeysShiftEnter = []byte{0x1b, '[', '2', '7', ';', '2', ';', '1', '3', '~'}
	kittyShiftEnter   = []byte{0x1b, '[', '1', '3', ';', '2', 'u'}
	altEnter          = []byte{0x1b, '\r'}
)

func (r *shiftEnterReader) Read(p []byte) (int, error) {
	// Read fresh bytes into a temp buffer
	tmp := make([]byte, len(p))
	n, err := r.src.Read(tmp)

	// Combine carry-over with fresh input
	all := append(r.hold, tmp[:n]...)
	r.hold = nil

	out := bytes.NewBuffer(make([]byte, 0, len(all)))
	i := 0
	for i < len(all) {
		// Try to match each known sequence at i
		matched := false
		for _, seq := range [][]byte{modKeysShiftEnter, kittyShiftEnter} {
			if i+len(seq) <= len(all) && bytes.Equal(all[i:i+len(seq)], seq) {
				out.Write(altEnter)
				i += len(seq)
				matched = true
				break
			}
		}
		if matched {
			continue
		}

		// If the current byte starts a possibly-incomplete escape sequence we
		// care about, hold it for the next Read so we can match across reads.
		// We hold any ESC '[' prefix that has no CSI terminator yet — the
		// terminator is a final byte 0x40..0x7E in standard CSI.
		if all[i] == 0x1b {
			// Worst case the sequences we care about are 10 bytes
			if len(all)-i < len(modKeysShiftEnter) {
				if startsPossibleShiftEnter(all[i:]) {
					r.hold = append(r.hold, all[i:]...)
					i = len(all)
					break
				}
			}
		}

		out.WriteByte(all[i])
		i++
	}

	// Copy out to p. If somehow the translated output is larger than p
	// (can't happen here since we only ever shrink), re-buffer the surplus.
	nn := copy(p, out.Bytes())
	if nn < out.Len() {
		r.hold = append(out.Bytes()[nn:], r.hold...)
	}
	if nn == 0 && err == nil && len(r.hold) > 0 {
		// We held everything; next read will pick it up
		return 0, nil
	}
	return nn, err
}

// startsPossibleShiftEnter returns true if the (incomplete) byte prefix could
// still grow into one of the sequences we translate. Cheap conservative check.
func startsPossibleShiftEnter(b []byte) bool {
	if len(b) == 0 || b[0] != 0x1b {
		return false
	}
	if len(b) == 1 {
		return true
	}
	if b[1] != '[' {
		return false
	}
	// Could be either "27;2;13~" or "13;2u" — both start "[1" or "[2"
	// We accept any digit/semicolon/incomplete state as a possible match.
	for j := 2; j < len(b); j++ {
		c := b[j]
		if c == '~' || c == 'u' {
			return false // complete — wouldn't be held
		}
		if !(c >= '0' && c <= '9') && c != ';' {
			return false
		}
	}
	return true
}
