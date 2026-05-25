package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"
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

	m := newModel(apiURL, stateDir)
	p := tea.NewProgram(m,
		tea.WithAltScreen(),
		tea.WithInput(os.Stdin),
		tea.WithOutput(os.Stdout),
	)
	if _, err := p.Run(); err != nil {
		log.Printf("run error: %v", err)
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	log.Println("exited cleanly")
}
