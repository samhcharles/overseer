package main

import "github.com/charmbracelet/lipgloss"

var (
	colAccent = lipgloss.Color("#e06c00")
	colDim    = lipgloss.Color("#555555")
	colBright = lipgloss.Color("#d0d0d0")
	colGreen  = lipgloss.Color("#73F59F")
	colRed    = lipgloss.Color("#FF5F57")
	colBlue   = lipgloss.Color("#74B9E7")

	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(colAccent)

	badgeStyle = lipgloss.NewStyle().
			Padding(0, 1).
			Background(lipgloss.Color("#2a2a2a")).
			Foreground(colBright)

	userLabelStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(colAccent)

	asstLabelStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#888888"))

	inputBoxStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(colDim).
			Padding(0, 1)

	statusStyle = lipgloss.NewStyle().
			Foreground(colDim)

	selectedStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(colAccent)

	fileStyle = lipgloss.NewStyle().
			Foreground(colBright)

	dirStyle = lipgloss.NewStyle().
			Foreground(colBlue)

	hintStyle = lipgloss.NewStyle().
			Foreground(colDim)

	errStyle = lipgloss.NewStyle().
			Foreground(colRed)

	sepStyle = lipgloss.NewStyle().
			Foreground(colDim)
)

func statusDot(ok bool) string {
	if ok {
		return lipgloss.NewStyle().Foreground(colGreen).Render("●")
	}
	return lipgloss.NewStyle().Foreground(colRed).Render("●")
}
