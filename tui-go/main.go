package main

import (
	"fmt"
	"os"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"
)

func main() {
	apiURL := os.Getenv("OVERSEER_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:7860"
	}

	vaultPath := os.Getenv("VAULT_PATH")
	if vaultPath == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			fmt.Fprintln(os.Stderr, "cannot determine home directory")
			os.Exit(1)
		}
		vaultPath = filepath.Join(home, "vault")
	}

	m := newModel(apiURL, vaultPath)
	p := tea.NewProgram(m, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}
