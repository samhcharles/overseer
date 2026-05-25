package main

import "github.com/charmbracelet/lipgloss"

var (
	colAccent = lipgloss.Color("#e06c00")
	colDim    = lipgloss.Color("#555555")
	colBright = lipgloss.Color("#d0d0d0")
	colGreen  = lipgloss.Color("#73F59F")
	colRed    = lipgloss.Color("#FF5F57")
	colBlue   = lipgloss.Color("#74B9E7")
	colYellow = lipgloss.Color("#f0c040")

	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(colAccent)

	userLabelStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(colAccent)

	asstLabelStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#888888"))

	statusStyle = lipgloss.NewStyle().
			Foreground(colDim)

	hintStyle = lipgloss.NewStyle().
			Foreground(colDim)

	errStyle = lipgloss.NewStyle().
			Foreground(colRed)

	sepStyle = lipgloss.NewStyle().
			Foreground(colDim)

	selectedStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(colAccent)

	// Activity panel
	panelTitleStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#666666"))

	activityDoneStyle = lipgloss.NewStyle().
				Foreground(colGreen)

	activityActiveStyle = lipgloss.NewStyle().
				Foreground(colYellow)

	statsStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#888888"))

	// Scroll indicator
	scrollStyle = lipgloss.NewStyle().
			Foreground(colYellow)
)

func statusDot(ok bool) string {
	if ok {
		return lipgloss.NewStyle().Foreground(colGreen).Render("●")
	}
	return lipgloss.NewStyle().Foreground(colRed).Render("●")
}
