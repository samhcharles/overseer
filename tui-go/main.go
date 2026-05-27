package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"
)

// Terminal control sequence to make Shift+Enter a distinguishable key
// via xterm's modify-other-keys protocol (level 1). Supported by xterm,
// Windows Terminal 1.18+, WezTerm, Kitty, Ghostty, iTerm2, and Alacritty.
//
// Once enabled, Shift+Enter emits `\x1b[27;2;13~` while plain Enter still
// emits `\r`. The input wrapper (see input_wrapper.go) translates the
// shift sequence to ESC+CR, which Bubble Tea parses as Alt+Enter — handled
// by the model as a newline insert.
//
// We deliberately do NOT enable the Kitty keyboard protocol (`\x1b[>1u`)
// because its "disambiguate" flag makes plain Enter send `\x1b[13u`, which
// older versions of Bubble Tea don't parse — breaking the most fundamental
// keystroke in the TUI.
const (
	enableShiftEnterDetection  = "\x1b[>4;1m"
	disableShiftEnterDetection = "\x1b[>4;0m"
)

func main() {
	f, err := os.OpenFile("/tmp/overseer.log", os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0600)
	if err == nil {
		log.SetOutput(f)
		defer f.Close()
	}

	apiURL := os.Getenv("OVERSEER_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:8765"
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
