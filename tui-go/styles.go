package main

import "github.com/charmbracelet/lipgloss"

// Package-level style vars are rebuilt by applyTheme() any time the
// active theme changes. Old code that reads colAccent/hintStyle/etc.
// keeps working — values just refresh on theme switch.

var (
	colAccent lipgloss.Color
	colDim    lipgloss.Color
	colBright lipgloss.Color
	colGreen  lipgloss.Color
	colRed    lipgloss.Color
	colBlue   lipgloss.Color
	colYellow lipgloss.Color
	colMuted  lipgloss.Color
	colBgAlt  lipgloss.Color

	titleStyle          lipgloss.Style
	userLabelStyle      lipgloss.Style
	asstLabelStyle      lipgloss.Style
	statusStyle         lipgloss.Style
	hintStyle           lipgloss.Style
	errStyle            lipgloss.Style
	sepStyle            lipgloss.Style
	selectedStyle       lipgloss.Style
	panelTitleStyle     lipgloss.Style
	activityDoneStyle   lipgloss.Style
	activityActiveStyle lipgloss.Style
	activityErrStyle    lipgloss.Style
	statsStyle          lipgloss.Style
	scrollStyle         lipgloss.Style
	cancelStyle         lipgloss.Style
	badgeStyle          lipgloss.Style
	bodyStyle           lipgloss.Style
	inputBoxStyle       lipgloss.Style
	cursorStyle         lipgloss.Style
)

func init() {
	loadPersistedTheme()
	applyTheme()
}

func applyTheme() {
	t := activeTheme

	colAccent = t.Accent
	colDim = t.Dim
	colBright = t.Bright
	colGreen = t.Green
	colRed = t.Red
	colBlue = t.Blue
	colYellow = t.Yellow
	colMuted = t.Muted
	colBgAlt = t.BgAlt

	titleStyle = lipgloss.NewStyle().Bold(true).Foreground(colAccent)
	userLabelStyle = lipgloss.NewStyle().Bold(true).Foreground(colAccent)
	asstLabelStyle = lipgloss.NewStyle().Bold(true).Foreground(colMuted)
	statusStyle = lipgloss.NewStyle().Foreground(colDim)
	hintStyle = lipgloss.NewStyle().Foreground(colDim)
	errStyle = lipgloss.NewStyle().Foreground(colRed)
	sepStyle = lipgloss.NewStyle().Foreground(colDim)
	selectedStyle = lipgloss.NewStyle().Bold(true).Foreground(colAccent)
	panelTitleStyle = lipgloss.NewStyle().Foreground(colMuted)
	activityDoneStyle = lipgloss.NewStyle().Foreground(colGreen)
	activityActiveStyle = lipgloss.NewStyle().Foreground(colYellow)
	activityErrStyle = lipgloss.NewStyle().Foreground(colRed)
	statsStyle = lipgloss.NewStyle().Foreground(colMuted)
	scrollStyle = lipgloss.NewStyle().Foreground(colYellow)
	cancelStyle = lipgloss.NewStyle().Foreground(colYellow).Italic(true)
	badgeStyle = lipgloss.NewStyle().Foreground(colDim).Italic(true)
	bodyStyle = lipgloss.NewStyle().Foreground(colBright)
	inputBoxStyle = lipgloss.NewStyle().Foreground(colBright)
	cursorStyle = lipgloss.NewStyle().Foreground(colAccent).Bold(true)
}

func statusDot(ok bool) string {
	if ok {
		return lipgloss.NewStyle().Foreground(colGreen).Render("●")
	}
	return lipgloss.NewStyle().Foreground(colRed).Render("●")
}
