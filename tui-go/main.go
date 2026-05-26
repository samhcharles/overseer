package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"
)

// Terminal control sequences used to make Shift+Enter a distinguishable key:
//   - modify-other-keys level 1 (xterm / Windows Terminal / most modern):
//       enable:  \x1b[>4;1m
//       disable: \x1b[>4;0m
//   - Kitty keyboard protocol (Kitty / WezTerm with kitty-mode / ghostty):
//       enable:  \x1b[>1u   (push flags onto stack)
//       disable: \x1b[<u    (pop flags)
//
// Once enabled, the terminal emits a distinct sequence for Shift+Enter that
// the input wrapper (see input_wrapper.go) translates to ESC+CR — which
// Bubble Tea then parses as Alt+Enter, and the model treats as newline.
const (
	enableShiftEnterDetection  = "\x1b[>4;1m\x1b[>1u"
	disableShiftEnterDetection = "\x1b[>4;0m\x1b[<u"
)

func main() {
	f, err := os.OpenFile("/tmp/overseer.log", os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0600)
	if err == nil {
		log.SetOutput(f)
		defer f.Close()
	}

	apiURL := os.Getenv("OVERSEER_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:7860"
	}

	stateDir := os.Getenv("OVERSEER_STATE_DIR")
	if stateDir == "" {
		home, _ := os.UserHomeDir()
		stateDir = filepath.Join(home, ".local", "state", "overseer")
	}

	log.Printf("starting: api=%s state=%s", apiURL, stateDir)

	// Enable distinguishable Shift+Enter on terminals that support it.
	// Writing to stdout BEFORE bubbletea enters alt-screen is fine — modes
	// are terminal state and survive the alt-screen switch.
	os.Stdout.WriteString(enableShiftEnterDetection)
	defer os.Stdout.WriteString(disableShiftEnterDetection)

	m := newModel(apiURL, stateDir)
	p := tea.NewProgram(m,
		tea.WithAltScreen(),
		tea.WithInput(newShiftEnterReader(os.Stdin)),
		tea.WithOutput(os.Stdout),
	)
	if _, err := p.Run(); err != nil {
		log.Printf("run error: %v", err)
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	log.Println("exited cleanly")
}
