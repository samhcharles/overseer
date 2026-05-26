package main

import (
	"bytes"
	"io"
	"strings"
	"testing"
)

func readAll(r io.Reader) []byte {
	var out []byte
	buf := make([]byte, 32)
	zeros := 0
	for {
		n, err := r.Read(buf)
		out = append(out, buf[:n]...)
		if err != nil {
			break
		}
		// Wrapper may return (0, nil) when it's holding an incomplete
		// sequence; retry a few times then bail to avoid spinning forever.
		if n == 0 {
			zeros++
			if zeros > 100 {
				break
			}
			continue
		}
		zeros = 0
	}
	return out
}

func TestShiftEnter_ModifyOtherKeys(t *testing.T) {
	in := bytes.NewReader([]byte("hi\x1b[27;2;13~there"))
	r := newShiftEnterReader(in)
	got := readAll(r)
	want := []byte("hi\x1b\rthere")
	if !bytes.Equal(got, want) {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestShiftEnter_KittyCSIu(t *testing.T) {
	in := bytes.NewReader([]byte("\x1b[13;2u"))
	r := newShiftEnterReader(in)
	got := readAll(r)
	want := []byte("\x1b\r")
	if !bytes.Equal(got, want) {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestShiftEnter_Passthrough(t *testing.T) {
	for _, s := range []string{"hello", "\x1b[A", "\r\n", "a\x1bb", "\x1b[27;5;13~"} {
		r := newShiftEnterReader(bytes.NewReader([]byte(s)))
		got := readAll(r)
		if !bytes.Equal(got, []byte(s)) {
			t.Errorf("passthrough failed for %q: got %q", s, got)
		}
	}
}

// chunkedReader returns its source one byte at a time so we can verify
// the wrapper's cross-Read buffering.
type chunkedReader struct {
	data []byte
	pos  int
}

func (c *chunkedReader) Read(p []byte) (int, error) {
	if c.pos >= len(c.data) {
		return 0, io.EOF
	}
	p[0] = c.data[c.pos]
	c.pos++
	return 1, nil
}

func TestShiftEnter_SplitAcrossReads(t *testing.T) {
	src := &chunkedReader{data: []byte("a\x1b[27;2;13~b")}
	r := newShiftEnterReader(src)
	got := readAll(r)
	want := []byte("a\x1b\rb")
	if !bytes.Equal(got, want) {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestShiftEnter_NoFalsePositive(t *testing.T) {
	// "\x1b[27;1;1~" is not a shift+enter — must pass through unchanged
	s := "\x1b[27;1;1~"
	r := newShiftEnterReader(strings.NewReader(s))
	got := readAll(r)
	if !bytes.Equal(got, []byte(s)) {
		t.Fatalf("got %q want %q", got, s)
	}
}
