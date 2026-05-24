package main

import (
	"crypto/rand"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type activeView int

const (
	chatView  activeView = iota
	vaultView
)

type chatMsg struct {
	role    string // "user" | "assistant"
	content string
}

type model struct {
	width, height int
	view          activeView

	// API
	apiURL   string
	apiOK    bool
	apiModel string

	// Chat
	messages []chatMsg
	msgVP    viewport.Model
	input    textarea.Model
	loading  bool
	threadID string
	sp       spinner.Model
	lastErr  string

	// Vault
	vaultRoot   string
	vaultCwd    string
	vaultFiles  []fs.DirEntry
	vaultCursor int
	previewVP   viewport.Model
}

func newModel(apiURL, vaultPath string) model {
	ta := textarea.New()
	ta.Placeholder = "message..."
	ta.CharLimit = 0
	ta.ShowLineNumbers = false
	ta.SetHeight(3)
	ta.SetWidth(80)

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colAccent)

	m := model{
		apiURL:    apiURL,
		vaultRoot: vaultPath,
		vaultCwd:  vaultPath,
		threadID:  genID(),
		input:     ta,
		sp:        sp,
		msgVP:     viewport.New(80, 20),
		previewVP: viewport.New(50, 20),
		messages: []chatMsg{
			{role: "assistant", content: "ready. ask me anything — i have your vault, recent activity, and context."},
		},
	}
	m.loadVaultDir()
	m.loadFilePreview()
	return m
}

func genID() string {
	b := make([]byte, 6)
	rand.Read(b)
	return fmt.Sprintf("%x", b)
}

// ─── Init ─────────────────────────────────────────────────────────────────────

func (m model) Init() tea.Cmd {
	return tea.Batch(
		m.input.Focus(),
		healthCheckCmd(m.apiURL),
	)
}

// ─── Update ───────────────────────────────────────────────────────────────────

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m = m.recalc()
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case chatResponseMsg:
		m.loading = false
		if msg.err != nil {
			m.lastErr = msg.err.Error()
		} else {
			m.messages = append(m.messages, chatMsg{
				role:    "assistant",
				content: msg.response,
			})
			if msg.model != "" {
				m.apiModel = msg.model
			}
			m.lastErr = ""
		}
		m.rebuildChat()
		return m, m.input.Focus()

	case healthMsg:
		m.apiOK = msg.ok
		if msg.model != "" {
			m.apiModel = msg.model
		}
		return m, nil

	case spinner.TickMsg:
		if !m.loading {
			return m, nil
		}
		var cmd tea.Cmd
		m.sp, cmd = m.sp.Update(msg)
		return m, cmd
	}

	// Delegate non-key events to active component
	var cmds []tea.Cmd
	if m.view == chatView && !m.loading {
		var c tea.Cmd
		m.input, c = m.input.Update(msg)
		cmds = append(cmds, c)
	}
	if m.view == vaultView {
		var c tea.Cmd
		m.previewVP, c = m.previewVP.Update(msg)
		cmds = append(cmds, c)
	}
	{
		var c tea.Cmd
		m.msgVP, c = m.msgVP.Update(msg)
		cmds = append(cmds, c)
	}
	return m, tea.Batch(cmds...)
}

func (m model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {

	case tea.KeyTab:
		if m.view == chatView {
			m.view = vaultView
			m.input.Blur()
		} else {
			m.view = chatView
			return m, m.input.Focus()
		}
		return m, nil

	case tea.KeyEsc:
		if m.loading {
			m.loading = false
			return m, m.input.Focus()
		}
		return m, nil

	case tea.KeyEnter:
		if m.view == chatView && !m.loading {
			text := strings.TrimSpace(m.input.Value())
			if text == "" {
				return m, nil
			}
			m.input.Reset()
			m.messages = append(m.messages, chatMsg{role: "user", content: text})
			m.loading = true
			m.lastErr = ""
			m.rebuildChat()
			return m, tea.Batch(
				m.sp.Tick,
				sendMessageCmd(m.apiURL, text, m.threadID),
			)
		}
		if m.view == vaultView {
			return m.vaultEnter()
		}
		return m, nil

	case tea.KeyCtrlJ:
		if m.view == chatView && !m.loading {
			m.input.InsertString("\n")
		}
		return m, nil

	case tea.KeyUp:
		if m.view == vaultView {
			if m.vaultCursor > 0 {
				m.vaultCursor--
				m.loadFilePreview()
			}
			return m, nil
		}
		m.msgVP.LineUp(3)
		return m, nil

	case tea.KeyDown:
		if m.view == vaultView {
			if m.vaultCursor < len(m.vaultFiles)-1 {
				m.vaultCursor++
				m.loadFilePreview()
			}
			return m, nil
		}
		m.msgVP.LineDown(3)
		return m, nil

	case tea.KeyPgUp:
		m.msgVP.ViewUp()
		return m, nil

	case tea.KeyPgDown:
		m.msgVP.ViewDown()
		return m, nil

	case tea.KeyRunes:
		if m.view == vaultView {
			switch msg.String() {
			case "-":
				parent := filepath.Dir(m.vaultCwd)
				if parent != m.vaultCwd && strings.HasPrefix(parent+"/", m.vaultRoot+"/") || parent == m.vaultRoot {
					m.vaultCwd = parent
					m.vaultCursor = 0
					m.loadVaultDir()
					m.loadFilePreview()
				}
				return m, nil
			}
		}
	}

	// Pass unhandled keys to the active component
	if m.view == chatView && !m.loading {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}
	if m.view == vaultView {
		var cmd tea.Cmd
		m.previewVP, cmd = m.previewVP.Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m model) vaultEnter() (tea.Model, tea.Cmd) {
	if len(m.vaultFiles) == 0 {
		return m, nil
	}
	f := m.vaultFiles[m.vaultCursor]
	if f.IsDir() {
		m.vaultCwd = filepath.Join(m.vaultCwd, f.Name())
		m.vaultCursor = 0
		m.loadVaultDir()
		m.loadFilePreview()
	}
	return m, nil
}

// ─── vault helpers ────────────────────────────────────────────────────────────

func (m *model) loadVaultDir() {
	entries, err := os.ReadDir(m.vaultCwd)
	if err != nil {
		m.vaultFiles = nil
		return
	}
	var dirs, files []fs.DirEntry
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		if e.IsDir() {
			dirs = append(dirs, e)
		} else {
			files = append(files, e)
		}
	}
	m.vaultFiles = append(dirs, files...)
	if m.vaultCursor >= len(m.vaultFiles) {
		m.vaultCursor = 0
	}
}

func (m *model) loadFilePreview() {
	if len(m.vaultFiles) == 0 {
		m.previewVP.SetContent("")
		return
	}
	f := m.vaultFiles[m.vaultCursor]
	path := filepath.Join(m.vaultCwd, f.Name())

	if f.IsDir() {
		entries, err := os.ReadDir(path)
		if err != nil {
			m.previewVP.SetContent("[cannot read directory]")
			return
		}
		var sb strings.Builder
		for _, e := range entries {
			if strings.HasPrefix(e.Name(), ".") {
				continue
			}
			if e.IsDir() {
				sb.WriteString(dirStyle.Render(e.Name()+"/") + "\n")
			} else {
				sb.WriteString(fileStyle.Render("  "+e.Name()) + "\n")
			}
		}
		if sb.Len() == 0 {
			sb.WriteString(hintStyle.Render("(empty)"))
		}
		m.previewVP.SetContent(sb.String())
	} else {
		data, err := os.ReadFile(path)
		if err != nil {
			m.previewVP.SetContent("[cannot read file]")
			return
		}
		lines := strings.Split(string(data), "\n")
		if len(lines) > 300 {
			lines = append(lines[:300], "... (truncated)")
		}
		m.previewVP.SetContent(strings.Join(lines, "\n"))
	}
	m.previewVP.GotoTop()
}

// ─── chat helpers ─────────────────────────────────────────────────────────────

func (m *model) rebuildChat() {
	if m.msgVP.Width == 0 {
		return
	}
	var sb strings.Builder
	wrapW := m.msgVP.Width - 2
	if wrapW < 20 {
		wrapW = 20
	}
	for _, msg := range m.messages {
		if msg.role == "user" {
			sb.WriteString(userLabelStyle.Render("you") + "\n")
		} else {
			sb.WriteString(asstLabelStyle.Render("overseer") + "\n")
		}
		sb.WriteString(lipgloss.NewStyle().Width(wrapW).Render(msg.content))
		sb.WriteString("\n\n")
	}
	if m.lastErr != "" {
		sb.WriteString(errStyle.Render("error: "+m.lastErr) + "\n")
	}
	m.msgVP.SetContent(sb.String())
	m.msgVP.GotoBottom()
}

// ─── layout ───────────────────────────────────────────────────────────────────

func (m model) recalc() model {
	// Input area: textarea rows + 2 border + 1 padding line above
	inputH := m.input.Height() + 3
	if inputH < 5 {
		inputH = 5
	}

	// Chat content: full height minus header, status, separator, input
	chatContentH := m.height - 1 - 1 - 1 - inputH
	if chatContentH < 3 {
		chatContentH = 3
	}

	// Vault content: full height minus header, status
	vaultContentH := m.height - 1 - 1 - 1
	if vaultContentH < 3 {
		vaultContentH = 3
	}

	m.msgVP.Width = m.width
	m.msgVP.Height = chatContentH

	leftW := m.width / 3
	if leftW < 20 {
		leftW = 20
	}
	rightW := m.width - leftW - 1
	if rightW < 10 {
		rightW = 10
	}
	m.previewVP.Width = rightW - 2
	m.previewVP.Height = vaultContentH

	m.input.SetWidth(m.width - 4)
	m.rebuildChat()
	return m
}

// ─── View ─────────────────────────────────────────────────────────────────────

func (m model) View() string {
	if m.width == 0 {
		return "initializing..."
	}

	header := m.renderHeader()
	status := m.renderStatus()

	var content string
	switch m.view {
	case chatView:
		content = m.renderChat()
	case vaultView:
		content = m.renderVault()
	}

	return header + "\n" + content + "\n" + status
}

func (m model) renderHeader() string {
	var badge string
	switch m.view {
	case chatView:
		badge = badgeStyle.Render("chat")
	case vaultView:
		badge = badgeStyle.Render("vault")
	}

	modelLabel := ""
	if m.apiModel != "" {
		modelLabel = "  " + hintStyle.Render(m.apiModel)
	}

	left := titleStyle.Render("overseer") + "  " + badge + modelLabel
	right := statusDot(m.apiOK)

	gap := m.width - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 0 {
		gap = 0
	}
	return left + strings.Repeat(" ", gap) + right
}

func (m model) renderStatus() string {
	var hints string
	switch m.view {
	case chatView:
		hints = "enter:send   ctrl+j:newline   pgup/pgdn:scroll   tab:vault   ctrl+c:quit"
	case vaultView:
		hints = "tab:chat   ↑↓:navigate   enter:open   -:parent   ctrl+c:quit"
	}
	return statusStyle.Render(hints)
}

func (m model) renderChat() string {
	sep := sepStyle.Render(strings.Repeat("─", m.width))

	var inputArea string
	if m.loading {
		inputArea = inputBoxStyle.Width(m.width - 2).Render(
			m.sp.View() + " thinking...",
		)
	} else {
		inputArea = inputBoxStyle.Width(m.width - 2).Render(m.input.View())
	}

	return m.msgVP.View() + "\n" + sep + "\n" + inputArea
}

func (m model) renderVault() string {
	leftW := m.width / 3
	if leftW < 20 {
		leftW = 20
	}

	// Left panel: file list
	var list strings.Builder
	relPath, _ := filepath.Rel(m.vaultRoot, m.vaultCwd)
	if relPath == "." {
		relPath = "/"
	} else {
		relPath = "/" + relPath
	}
	list.WriteString(hintStyle.Render(relPath) + "\n")
	list.WriteString(hintStyle.Render(strings.Repeat("─", leftW-1)) + "\n")

	maxName := leftW - 4
	for i, f := range m.vaultFiles {
		name := f.Name()
		if f.IsDir() {
			name += "/"
		}
		if len(name) > maxName {
			name = name[:maxName-1] + "~"
		}

		var line string
		if i == m.vaultCursor {
			line = selectedStyle.Render("> " + name)
		} else if f.IsDir() {
			line = "  " + dirStyle.Render(name)
		} else {
			line = "  " + fileStyle.Render(name)
		}
		list.WriteString(line + "\n")
	}

	leftPanel := lipgloss.NewStyle().
		Width(leftW).
		Height(m.previewVP.Height + 2).
		Render(list.String())

	// Divider column
	divLines := make([]string, m.previewVP.Height+2)
	for i := range divLines {
		divLines[i] = sepStyle.Render("│")
	}
	divider := strings.Join(divLines, "\n")

	// Right panel: preview
	rightPanel := lipgloss.NewStyle().
		Width(m.previewVP.Width + 2).
		Padding(0, 1).
		Render(m.previewVP.View())

	return lipgloss.JoinHorizontal(lipgloss.Top, leftPanel, divider, rightPanel)
}
