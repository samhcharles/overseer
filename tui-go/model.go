package main

import (
	"bufio"
	"crypto/rand"
	"fmt"
	"io"
	"regexp"
	"strings"
	"time"

	"github.com/atotto/clipboard"
	"github.com/charmbracelet/bubbles/cursor"
	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// ── view states ───────────────────────────────────────────────────────────────

type activeView int

const (
	startView activeView = iota
	chatView
	sessionView
	helpView
)

// ── data types ────────────────────────────────────────────────────────────────

type chatMsg struct {
	role    string
	content string
	ts      time.Time
}

type activityEntry struct {
	name     string
	args     string
	preview  string
	done     bool
	err      bool
	expanded bool
}

type sessionInfo struct {
	id       string
	preview  string
	modified time.Time
}

// ── model ─────────────────────────────────────────────────────────────────────

type model struct {
	width, height int
	view          activeView
	prevView      activeView // remembered when help overlay opens

	apiURL   string
	apiOK    bool
	apiModel string

	messages      []chatMsg
	input         textarea.Model
	loading       bool
	threadID      string
	turnCount     int
	sp            spinner.Model
	lastErr       string
	streamScanner *bufio.Scanner
	streamBody    io.ReadCloser

	scrollOffset int // 0 = bottom; N = N rows above bottom

	activityLog       []activityEntry
	activityCursor    int // for expand toggle via Ctrl+E
	tokensTotal       int
	tps               float64

	sessions      []sessionInfo
	sessionCursor int
	stateDir      string

	// Slash palette
	slashOpen    bool
	slashMatches []slashCmd
	slashCursor  int

	// Input history
	inputHistory  []string
	historyCursor int // -1 = not navigating
	historySaved  string
}

func newModel(apiURL, stateDir string) model {
	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colAccent)

	ta := textarea.New()
	ta.Placeholder = "ask anything — type / for commands"
	ta.Prompt = "❯ "
	ta.CharLimit = 0
	ta.SetWidth(80)
	ta.SetHeight(1)
	ta.ShowLineNumbers = false
	ta.FocusedStyle.Prompt = lipgloss.NewStyle().Foreground(colAccent).Bold(true)
	ta.FocusedStyle.Text = lipgloss.NewStyle().Foreground(colBright)
	ta.FocusedStyle.Placeholder = lipgloss.NewStyle().Foreground(colDim).Italic(true)
	ta.BlurredStyle = ta.FocusedStyle
	ta.Cursor.Style = lipgloss.NewStyle().Foreground(colAccent)
	ta.KeyMap.InsertNewline.SetEnabled(false) // we handle Alt+Enter ourselves
	ta.Focus()

	return model{
		apiURL:        apiURL,
		stateDir:      stateDir,
		threadID:      genID(),
		sp:            sp,
		view:          startView,
		input:         ta,
		inputHistory:  loadInputHistory(),
		historyCursor: -1,
		activityCursor: -1,
	}
}

func genID() string {
	b := make([]byte, 6)
	rand.Read(b)
	return fmt.Sprintf("%x", b)
}

func shortID(id string) string {
	if len(id) > 6 {
		return id[:6]
	}
	return id
}

// ── Init ──────────────────────────────────────────────────────────────────────

func (m model) Init() tea.Cmd {
	return tea.Batch(
		healthCheckCmd(m.apiURL),
		loadSessionsCmd(m.stateDir),
		textarea.Blink,
	)
}

// ── Update ────────────────────────────────────────────────────────────────────

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.input.SetWidth(m.inputWidth())
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case healthMsg:
		m.apiOK = msg.ok
		if msg.model != "" {
			m.apiModel = msg.model
		}
		return m, nil

	case sessionsLoadedMsg:
		m.sessions = msg.sessions
		return m, nil

	case threadLoadedMsg:
		if msg.err != nil {
			m.lastErr = msg.err.Error()
		} else {
			m.threadID = msg.threadID
			m.messages = msg.messages
			m.turnCount = countTurns(msg.messages)
			m.activityLog = nil
			m.scrollOffset = 0
			m.tokensTotal = 0
			m.tps = 0
			m.lastErr = ""
		}
		m.view = chatView
		return m, nil

	case streamStartMsg:
		m.streamScanner = msg.scanner
		m.streamBody = msg.body
		return m, readChunkCmd(m.streamScanner, m.streamBody)

	case chunkMsg:
		return m.handleChunk(msg)

	case spinner.TickMsg:
		if !m.loading {
			return m, nil
		}
		var cmd tea.Cmd
		m.sp, cmd = m.sp.Update(msg)
		return m, cmd

	case cursor.BlinkMsg:
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}

	return m, nil
}

func countTurns(msgs []chatMsg) int {
	n := 0
	for _, m := range msgs {
		if m.role == "user" {
			n++
		}
	}
	return n
}

func (m model) handleChunk(msg chunkMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		m.loading = false
		m.lastErr = msg.err.Error()
		m.streamScanner = nil
		m.streamBody = nil
		if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" && m.messages[n-1].content == "" {
			m.messages = m.messages[:n-1]
		}
		return m, nil
	}

	if msg.done {
		m.loading = false
		m.streamScanner = nil
		m.streamBody = nil
		if msg.model != "" {
			m.apiModel = msg.model
		}
		if msg.tokens > 0 {
			m.tokensTotal += msg.tokens
		}
		if msg.tps > 0 {
			m.tps = msg.tps
		}
		return m, nil
	}

	if msg.content != "" {
		// Defensive: strip any @@TOOL@@{...} markers that slipped through the
		// server-side filter. The model sometimes emits them mid-sentence.
		clean := stripToolMarkers(msg.content)
		if clean != "" {
			if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" {
				m.messages[n-1].content += clean
			}
		}
		return m, readChunkCmd(m.streamScanner, m.streamBody)
	}

	if msg.toolDone {
		for i := len(m.activityLog) - 1; i >= 0; i-- {
			if m.activityLog[i].name == msg.toolName && !m.activityLog[i].done {
				m.activityLog[i].done = true
				m.activityLog[i].preview = msg.toolPreview
				if strings.HasPrefix(strings.ToLower(msg.toolPreview), "error") {
					m.activityLog[i].err = true
				}
				break
			}
		}
		return m, readChunkCmd(m.streamScanner, m.streamBody)
	}

	if msg.toolName != "" {
		m.activityLog = append(m.activityLog, activityEntry{
			name: msg.toolName,
			args: msg.toolArgs,
		})
		if len(m.activityLog) > 50 {
			m.activityLog = m.activityLog[len(m.activityLog)-50:]
		}
		return m, readChunkCmd(m.streamScanner, m.streamBody)
	}

	return m, readChunkCmd(m.streamScanner, m.streamBody)
}

// ── Key handling ──────────────────────────────────────────────────────────────

func (m model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch m.view {
	case startView:
		return m.handleStartKey(msg)
	case sessionView:
		return m.handleSessionKey(msg)
	case helpView:
		return m.handleHelpKey(msg)
	default:
		return m.handleChatKey(msg)
	}
}

func (m model) handleStartKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if msg.Type == tea.KeyCtrlC {
		return m, tea.Quit
	}
	if msg.Type == tea.KeyEnter {
		if len(m.sessions) > 0 {
			return m, loadThreadCmd(m.apiURL, m.sessions[0].id)
		}
	}
	if msg.Type == tea.KeyRunes {
		switch msg.String() {
		case "l", "L":
			if len(m.sessions) > 0 {
				m.view = sessionView
				m.sessionCursor = 0
				return m, nil
			}
		case "?":
			m.prevView = startView
			m.view = helpView
			return m, nil
		}
	}
	m.view = chatView
	m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?", ts: time.Now()}}
	return m, nil
}

func (m model) handleSessionKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC:
		return m, tea.Quit
	case tea.KeyEsc:
		m.view = chatView
		if len(m.messages) == 0 {
			m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?", ts: time.Now()}}
		}
		return m, nil
	case tea.KeyUp:
		if m.sessionCursor > 0 {
			m.sessionCursor--
		}
		return m, nil
	case tea.KeyDown:
		if m.sessionCursor < len(m.sessions)-1 {
			m.sessionCursor++
		}
		return m, nil
	case tea.KeyEnter:
		if len(m.sessions) > 0 {
			return m, loadThreadCmd(m.apiURL, m.sessions[m.sessionCursor].id)
		}
		m.view = chatView
		return m, nil
	case tea.KeyRunes:
		switch msg.String() {
		case "n", "N":
			return m.resetThread()
		case "?":
			m.prevView = sessionView
			m.view = helpView
			return m, nil
		}
	}
	return m, nil
}

func (m model) handleHelpKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if msg.Type == tea.KeyCtrlC {
		return m, tea.Quit
	}
	if msg.Type == tea.KeyEsc || (msg.Type == tea.KeyRunes && (msg.String() == "?" || msg.String() == "q")) {
		m.view = m.prevView
		if m.view == helpView {
			m.view = chatView
		}
		return m, nil
	}
	return m, nil
}

func (m model) handleChatKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	// Slash palette consumes navigation keys when open
	if m.slashOpen {
		return m.handleSlashKey(msg)
	}

	switch msg.Type {
	case tea.KeyCtrlC:
		return m, tea.Quit

	case tea.KeyEsc:
		if m.loading {
			return m.cancelStream()
		}
		return m, nil

	case tea.KeyCtrlN:
		return m.resetThread()

	case tea.KeyCtrlL:
		m.view = sessionView
		m.sessionCursor = 0
		return m, loadSessionsCmd(m.stateDir)

	case tea.KeyPgUp:
		m.scrollOffset += 10
		return m, nil

	case tea.KeyPgDown:
		if m.scrollOffset > 10 {
			m.scrollOffset -= 10
		} else {
			m.scrollOffset = 0
		}
		return m, nil

	case tea.KeyCtrlE:
		// Toggle expansion of last activity entry
		if len(m.activityLog) > 0 {
			i := len(m.activityLog) - 1
			m.activityLog[i].expanded = !m.activityLog[i].expanded
		}
		return m, nil

	case tea.KeyCtrlY:
		// Copy last assistant message to system clipboard
		for i := len(m.messages) - 1; i >= 0; i-- {
			if m.messages[i].role == "assistant" && m.messages[i].content != "" {
				if err := clipboard.WriteAll(m.messages[i].content); err == nil {
					m.lastErr = ""
					// Surface as a transient assistant footer line
					m.messages = append(m.messages, chatMsg{
						role:    "assistant",
						content: "_copied to clipboard_",
						ts:      time.Now(),
					})
				} else {
					m.lastErr = "clipboard: " + err.Error()
				}
				break
			}
		}
		return m, nil

	}

	// History navigation (only when input is single-line empty)
	if m.isInputEmpty() && !m.loading {
		switch msg.Type {
		case tea.KeyUp:
			if len(m.inputHistory) == 0 {
				return m, nil
			}
			if m.historyCursor == -1 {
				m.historySaved = m.input.Value()
				m.historyCursor = len(m.inputHistory) - 1
			} else if m.historyCursor > 0 {
				m.historyCursor--
			}
			m.input.SetValue(m.inputHistory[m.historyCursor])
			m.input.CursorEnd()
			return m, nil
		case tea.KeyDown:
			if m.historyCursor == -1 {
				m.scrollIfPossible(-1)
				return m, nil
			}
			if m.historyCursor < len(m.inputHistory)-1 {
				m.historyCursor++
				m.input.SetValue(m.inputHistory[m.historyCursor])
				m.input.CursorEnd()
			} else {
				m.historyCursor = -1
				m.input.SetValue(m.historySaved)
				m.input.CursorEnd()
				m.historySaved = ""
			}
			return m, nil
		}
	}

	// Scroll up/down when input has content (treat as scroll)
	if msg.Type == tea.KeyUp && m.scrollOffset >= 0 {
		// only treat as scroll when cursor is on first line of textarea
		if m.input.Line() == 0 && len(m.input.Value()) > 0 {
			m.scrollOffset++
			return m, nil
		}
	}
	if msg.Type == tea.KeyDown && m.scrollOffset > 0 {
		if m.input.Line() == m.input.LineCount()-1 && len(m.input.Value()) > 0 {
			m.scrollOffset--
			return m, nil
		}
	}

	// Submit
	if msg.Type == tea.KeyEnter && !msg.Alt {
		if m.loading {
			return m, nil
		}
		text := strings.TrimSpace(m.input.Value())
		if text == "" {
			return m, nil
		}
		m.input.Reset()
		m.historyCursor = -1
		m.historySaved = ""
		m.inputHistory = appendInputHistory(m.inputHistory, text)
		if strings.HasPrefix(text, "/") {
			return m.executeSlash(text)
		}
		return m.sendChat(text)
	}

	// Alt+Enter or Ctrl+J → newline within textarea
	if (msg.Type == tea.KeyEnter && msg.Alt) || msg.Type == tea.KeyCtrlJ {
		m.input.SetValue(m.input.Value() + "\n")
		// Grow input height if needed (cap at 8 lines)
		h := m.input.LineCount()
		if h < 1 {
			h = 1
		}
		if h > 8 {
			h = 8
		}
		m.input.SetHeight(h)
		return m, nil
	}

	// Detect "/" with empty buffer → open palette
	if m.isInputEmpty() && msg.Type == tea.KeyRunes && msg.String() == "/" {
		m.input.SetValue("/")
		m.input.CursorEnd()
		m.slashOpen = true
		m.slashMatches = matchSlash("/")
		m.slashCursor = 0
		return m, nil
	}

	// Default — pass to textarea
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)

	// Auto-grow textarea up to 8 lines
	if m.input.LineCount() != m.input.Height() {
		h := m.input.LineCount()
		if h < 1 {
			h = 1
		}
		if h > 8 {
			h = 8
		}
		m.input.SetHeight(h)
	}

	// Any keystroke that mutates input exits history nav
	if m.historyCursor != -1 {
		m.historyCursor = -1
		m.historySaved = ""
	}
	return m, cmd
}

func (m *model) scrollIfPossible(delta int) {
	if delta < 0 && m.scrollOffset > 0 {
		m.scrollOffset += delta
		if m.scrollOffset < 0 {
			m.scrollOffset = 0
		}
	} else if delta > 0 {
		m.scrollOffset += delta
	}
}

func (m model) isInputEmpty() bool {
	return strings.TrimSpace(m.input.Value()) == ""
}

func (m model) cancelStream() (tea.Model, tea.Cmd) {
	m.loading = false
	if m.streamBody != nil {
		m.streamBody.Close()
		m.streamBody = nil
		m.streamScanner = nil
	}
	if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" {
		if m.messages[n-1].content == "" {
			m.messages = m.messages[:n-1]
		} else {
			m.messages[n-1].content = strings.TrimRight(m.messages[n-1].content, " \n") + "\n\n*[cancelled]*"
		}
	}
	return m, nil
}

// ── Slash palette ──────────────────────────────────────────────────────────────

func (m model) handleSlashKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC:
		return m, tea.Quit

	case tea.KeyEsc:
		m.slashOpen = false
		m.slashMatches = nil
		return m, nil

	case tea.KeyUp:
		if m.slashCursor > 0 {
			m.slashCursor--
		}
		return m, nil

	case tea.KeyDown:
		if m.slashCursor < len(m.slashMatches)-1 {
			m.slashCursor++
		}
		return m, nil

	case tea.KeyTab:
		if len(m.slashMatches) > 0 {
			sel := m.slashMatches[m.slashCursor]
			m.input.SetValue(sel.name + " ")
			m.input.CursorEnd()
			m.slashOpen = false
			m.slashMatches = nil
		}
		return m, nil

	case tea.KeyEnter:
		if len(m.slashMatches) == 0 {
			m.slashOpen = false
			return m, nil
		}
		typed := strings.TrimSpace(m.input.Value())
		sel := m.slashMatches[m.slashCursor]
		var cmdLine string
		if strings.HasPrefix(typed, sel.name+" ") || typed == sel.name {
			cmdLine = typed
		} else {
			cmdLine = sel.name
		}
		m.input.Reset()
		m.slashOpen = false
		m.slashMatches = nil
		m.historyCursor = -1
		m.inputHistory = appendInputHistory(m.inputHistory, cmdLine)
		return m.executeSlash(cmdLine)

	case tea.KeyBackspace, tea.KeyDelete:
		v := m.input.Value()
		if len(v) > 0 {
			m.input.SetValue(v[:len(v)-1])
			m.input.CursorEnd()
		}
		nv := m.input.Value()
		if len(nv) == 0 || nv[0] != '/' {
			m.slashOpen = false
			m.slashMatches = nil
			return m, nil
		}
		m.slashMatches = matchSlash(nv)
		if m.slashCursor >= len(m.slashMatches) {
			m.slashCursor = 0
		}
		return m, nil

	case tea.KeySpace:
		m.input.SetValue(m.input.Value() + " ")
		m.input.CursorEnd()
		if cmd, _ := splitSlash(m.input.Value()); cmd != "" {
			if _, ok := findCmd(cmd); ok {
				m.slashOpen = false
				m.slashMatches = nil
			}
		}
		return m, nil

	case tea.KeyRunes:
		m.input.SetValue(m.input.Value() + msg.String())
		m.input.CursorEnd()
		m.slashMatches = matchSlash(m.input.Value())
		if len(m.slashMatches) == 0 {
			m.slashOpen = false
		}
		if m.slashCursor >= len(m.slashMatches) {
			m.slashCursor = 0
		}
		return m, nil
	}
	return m, nil
}

func (m model) executeSlash(line string) (tea.Model, tea.Cmd) {
	name, args := splitSlash(line)
	cmd, ok := findCmd(name)
	if !ok {
		m.lastErr = "unknown command: " + name + " (try /help)"
		return m, nil
	}
	switch cmd.localAction {
	case "quit":
		return m, tea.Quit
	case "new":
		return m.resetThread()
	case "sessions":
		m.view = sessionView
		m.sessionCursor = 0
		return m, loadSessionsCmd(m.stateDir)
	case "clear":
		m.messages = []chatMsg{{role: "assistant", content: "cleared. what's next?", ts: time.Now()}}
		m.activityLog = nil
		m.scrollOffset = 0
		m.lastErr = ""
		return m, nil
	case "help":
		m.prevView = chatView
		m.view = helpView
		return m, nil
	case "activity":
		var b strings.Builder
		if len(m.activityLog) == 0 {
			b.WriteString("_no tool activity this session yet._")
		} else {
			b.WriteString("### recent vault writes / tool calls\n\n")
			for _, e := range m.activityLog {
				mark := "·"
				if e.done {
					mark = "✓"
				}
				if e.err {
					mark = "✗"
				}
				b.WriteString("- " + mark + " **" + e.name + "**")
				if e.args != "" {
					b.WriteString(" — `" + trunc(e.args, 80) + "`")
				}
				b.WriteString("\n")
			}
		}
		m.messages = append(m.messages, chatMsg{role: "assistant", content: b.String(), ts: time.Now()})
		m.scrollOffset = 0
		return m, nil
	case "theme":
		if args == "" {
			m.messages = append(m.messages, chatMsg{
				role:    "assistant",
				content: "themes: **" + strings.Join(themeNames(), "**, **") + "**. current: **" + activeTheme.Name + "**. use `/theme <name>`.",
				ts:      time.Now(),
			})
			return m, nil
		}
		if !setTheme(args) {
			m.lastErr = "unknown theme: " + args
			return m, nil
		}
		m.refreshInputStyle()
		m.messages = append(m.messages, chatMsg{
			role:    "assistant",
			content: "theme switched to **" + activeTheme.Name + "**.",
			ts:      time.Now(),
		})
		return m, nil
	case "model":
		if args == "" {
			m.messages = append(m.messages, chatMsg{
				role:    "assistant",
				content: "current model: **" + m.apiModel + "**. use `/model <name>` to switch (Ollama).",
				ts:      time.Now(),
			})
			return m, nil
		}
		// fire-and-forget: PATCH apiURL/model with new name
		return m, switchModelCmd(m.apiURL, args)
	}
	if cmd.transform != nil {
		return m.sendChat(cmd.transform(args))
	}
	return m.sendChat(line)
}

func (m *model) refreshInputStyle() {
	m.input.FocusedStyle.Prompt = lipgloss.NewStyle().Foreground(colAccent).Bold(true)
	m.input.FocusedStyle.Text = lipgloss.NewStyle().Foreground(colBright)
	m.input.FocusedStyle.Placeholder = lipgloss.NewStyle().Foreground(colDim).Italic(true)
	m.input.Cursor.Style = lipgloss.NewStyle().Foreground(colAccent)
	m.sp.Style = lipgloss.NewStyle().Foreground(colAccent)
}

func (m model) sendChat(text string) (tea.Model, tea.Cmd) {
	now := time.Now()
	m.messages = append(m.messages, chatMsg{role: "user", content: text, ts: now})
	m.messages = append(m.messages, chatMsg{role: "assistant", content: "", ts: now})
	m.loading = true
	m.lastErr = ""
	m.scrollOffset = 0
	m.turnCount++
	return m, tea.Batch(m.sp.Tick, startStreamCmd(m.apiURL, text, m.threadID))
}

func (m model) resetThread() (tea.Model, tea.Cmd) {
	m.threadID = genID()
	m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?", ts: time.Now()}}
	m.activityLog = nil
	m.tokensTotal = 0
	m.tps = 0
	m.scrollOffset = 0
	m.turnCount = 0
	m.lastErr = ""
	m.input.Reset()
	return m, nil
}

// ── layout ────────────────────────────────────────────────────────────────────

const panelWidth = 28

func (m model) usePanel() bool { return m.width > 90 }

func (m model) leftWidth() int {
	w := m.width
	if w == 0 {
		w = 80
	}
	if m.usePanel() {
		return w - panelWidth - 1
	}
	return w
}

func (m model) inputWidth() int {
	w := m.leftWidth() - 2
	if w < 20 {
		w = 20
	}
	return w
}

// ── View dispatch ─────────────────────────────────────────────────────────────

func (m model) View() string {
	w := m.width
	if w == 0 {
		w = 80
	}
	h := m.height
	if h == 0 {
		h = 24
	}
	switch m.view {
	case startView:
		return m.renderStart(w, h)
	case sessionView:
		return m.renderSession(w, h)
	case helpView:
		return m.renderHelp(w, h)
	default:
		return m.renderMain(w, h)
	}
}

// ── Start screen ──────────────────────────────────────────────────────────────

// ASCII sprite — Overseer's signature mark. A watchful sentinel: stylized
// helmet/visor with a glowing inner eye and the wordmark beneath.
var spriteLines = []string{
	"    ╭───────────╮",
	"    │  ◢█████◣  │",
	"    │ ██  ◉  ██ │",
	"    │  ◥█████◤  │",
	"    ╰─────┬─────╯",
	"          ╵",
	"    O V E R S E E R",
}

func (m model) renderStart(w, h int) string {
	center := func(s string) string {
		n := lipgloss.Width(s)
		pad := (w - n) / 2
		if pad < 0 {
			pad = 0
		}
		return strings.Repeat(" ", pad) + s
	}

	var lines []string
	contentH := len(spriteLines) + 8
	topPad := (h - contentH) / 2
	if topPad < 0 {
		topPad = 0
	}
	for i := 0; i < topPad; i++ {
		lines = append(lines, "")
	}

	// Sprite with the eye accent-coloured
	for i, sl := range spriteLines {
		styled := sl
		switch i {
		case 1, 2, 3: // helmet outline rows
			styled = lipgloss.NewStyle().Foreground(colAccent).Render(sl)
		case 6: // wordmark
			styled = titleStyle.Render(sl)
		default:
			styled = sepStyle.Render(sl)
		}
		lines = append(lines, center(styled))
	}

	lines = append(lines, "")
	lines = append(lines, center(hintStyle.Render("your second brain · running local")))
	lines = append(lines, "")

	dot := statusDot(m.apiOK)
	modelStr := "connecting..."
	if m.apiModel != "" {
		modelStr = m.apiModel
	}
	state := "offline"
	if m.apiOK {
		state = "online"
	}
	lines = append(lines, center(dot+"  "+bodyStyle.Render(state)+"  "+hintStyle.Render(modelStr)))
	lines = append(lines, "")

	if len(m.sessions) > 0 {
		last := m.sessions[0]
		preview := last.preview
		if preview == "" {
			preview = last.id
		}
		lines = append(lines, center(hintStyle.Render(fmt.Sprintf("%d session(s) saved", len(m.sessions)))))
		lines = append(lines, center(hintStyle.Render("last: "+trunc(preview, 48))))
		lines = append(lines, "")
		lines = append(lines, center(badgeStyle.Render("enter")+hintStyle.Render(" → resume   ")+badgeStyle.Render("l")+hintStyle.Render(" → list   ")+badgeStyle.Render("?")+hintStyle.Render(" → help")))
		lines = append(lines, center(badgeStyle.Render("any other key")+hintStyle.Render(" → new session")))
	} else {
		lines = append(lines, center(hintStyle.Render("press any key to start    ? for help")))
	}

	for len(lines) < h {
		lines = append(lines, "")
	}
	return strings.Join(lines[:h], "\n")
}

// ── Session list ──────────────────────────────────────────────────────────────

func (m model) renderSession(w, h int) string {
	var lines []string

	hdr := titleStyle.Render("◉ sessions") + "   " +
		hintStyle.Render("↑↓:nav  enter:load  n:new  esc:back  ?:help  ctrl+c:quit")
	lines = append(lines, hdr)
	lines = append(lines, sepStyle.Render(strings.Repeat("─", w)))

	maxShow := h - 5
	for i, s := range m.sessions {
		if i >= maxShow {
			break
		}
		timeStr := s.modified.Format("Jan 02 15:04")
		preview := s.preview
		previewMax := w - 24
		if previewMax < 10 {
			previewMax = 10
		}
		if len(preview) > previewMax {
			preview = preview[:previewMax-3] + "..."
		}
		row := fmt.Sprintf("  %s  %s", hintStyle.Render(timeStr), preview)
		if i == m.sessionCursor {
			row = selectedStyle.Render("▶ ") + selectedStyle.Render(timeStr) + "  " + bodyStyle.Render(preview)
		}
		lines = append(lines, row)
	}

	if len(m.sessions) == 0 {
		lines = append(lines, "")
		lines = append(lines, "  "+hintStyle.Render("no sessions yet — press n to start a new one"))
	}

	for len(lines) < h {
		lines = append(lines, "")
	}
	return strings.Join(lines[:h], "\n")
}

// ── Help overlay ──────────────────────────────────────────────────────────────

func (m model) renderHelp(w, h int) string {
	var lines []string

	lines = append(lines, titleStyle.Render("◉ help")+"   "+hintStyle.Render("esc to close"))
	lines = append(lines, sepStyle.Render(strings.Repeat("─", w)))
	lines = append(lines, "")
	lines = append(lines, asstLabelStyle.Render("keys"))

	keyRows := [][2]string{
		{"enter", "send"},
		{"alt+enter / ctrl+j", "newline in input"},
		{"esc", "cancel stream (chat) / close (modal)"},
		{"↑ / ↓", "history (empty input) · scroll (else)"},
		{"pgup / pgdn", "scroll messages"},
		{"/", "open command palette"},
		{"tab", "complete selected command"},
		{"ctrl+n", "new thread"},
		{"ctrl+l", "session list"},
		{"ctrl+e", "toggle last activity entry"},
		{"ctrl+y", "yank (copy) last response to clipboard"},
		{"?", "this help"},
		{"ctrl+c", "quit"},
	}
	for _, kr := range keyRows {
		k := badgeStyle.Render(fmt.Sprintf("%-20s", kr[0]))
		lines = append(lines, "  "+k+"  "+kr[1])
	}

	lines = append(lines, "")
	lines = append(lines, asstLabelStyle.Render("commands"))
	for _, c := range slashCommands {
		name := selectedStyle.Render(fmt.Sprintf("%-12s", c.name))
		usage := ""
		if c.usage != "" {
			usage = " " + hintStyle.Render(c.usage)
		}
		lines = append(lines, "  "+name+usage+"  "+c.desc)
	}

	for len(lines) < h {
		lines = append(lines, "")
	}
	return strings.Join(lines[:h], "\n")
}

// ── Main chat view ────────────────────────────────────────────────────────────

func (m model) renderMain(w, h int) string {
	header := m.renderHeader(w)
	status := m.renderStatus(w)
	bodyH := h - 2
	if bodyH < 4 {
		bodyH = 4
	}

	lw := m.leftWidth()
	useP := m.usePanel()

	// Compute section heights inside bodyH.
	//
	//   bodyH = msgAreaH + paletteH + 1(sep) + inputH
	paletteLines := m.renderPalette(lw)
	paletteH := len(paletteLines)

	inputBlock := m.renderInput()
	inputLines := strings.Split(inputBlock, "\n")
	// Drop trailing empty line that some renderers append.
	for len(inputLines) > 1 && inputLines[len(inputLines)-1] == "" {
		inputLines = inputLines[:len(inputLines)-1]
	}
	inputH := len(inputLines)
	if inputH < 1 {
		inputH = 1
	}

	const sepH = 1
	msgAreaH := bodyH - paletteH - sepH - inputH
	if msgAreaH < 1 {
		msgAreaH = 1
	}

	chatLines := m.buildChatLines(lw, msgAreaH)

	var panelLines []string
	if useP {
		panelLines = m.buildPanelLines(panelWidth, bodyH)
	}

	var rows []string
	pRow := 0
	appendRow := func(left string) {
		if !useP {
			rows = append(rows, left)
			pRow++
			return
		}
		padN := lw - lipgloss.Width(left)
		if padN < 0 {
			padN = 0
		}
		right := ""
		if pRow < len(panelLines) {
			right = panelLines[pRow]
		}
		rows = append(rows, left+strings.Repeat(" ", padN)+sepStyle.Render("│ ")+right)
		pRow++
	}

	for i := 0; i < msgAreaH; i++ {
		left := ""
		if i < len(chatLines) {
			left = chatLines[i]
		}
		appendRow(left)
	}
	for _, pl := range paletteLines {
		appendRow(pl)
	}
	appendRow(sepStyle.Render(strings.Repeat("─", lw)))
	for _, il := range inputLines {
		appendRow(il)
	}

	return header + "\n" + strings.Join(rows, "\n") + "\n" + status
}

func (m model) renderHeader(w int) string {
	left := titleStyle.Render("◉ overseer")
	if m.apiModel != "" {
		left += "  " + hintStyle.Render(m.apiModel)
	}
	left += "  " + badgeStyle.Render("thread "+shortID(m.threadID))

	var rightParts []string
	if m.turnCount > 0 {
		rightParts = append(rightParts, hintStyle.Render(fmt.Sprintf("%d turns", m.turnCount)))
	}
	if m.tokensTotal > 0 {
		stat := fmt.Sprintf("%d tok", m.tokensTotal)
		if m.tps > 0 {
			stat += fmt.Sprintf(" · %.0f t/s", m.tps)
		}
		rightParts = append(rightParts, statsStyle.Render(stat))
	}
	if m.scrollOffset > 0 {
		rightParts = append(rightParts, scrollStyle.Render(fmt.Sprintf("↑%d", m.scrollOffset)))
	}
	rightParts = append(rightParts, statusDot(m.apiOK))
	right := strings.Join(rightParts, "  ")

	gap := w - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 1 {
		gap = 1
	}
	return left + strings.Repeat(" ", gap) + right
}

func (m model) renderStatus(w int) string {
	var hints string
	if m.loading {
		hints = "esc:cancel stream · ctrl+c:quit"
	} else {
		hints = "enter:send · alt+enter:newline · /:commands · ↑:history · ctrl+n:new · ctrl+l:sessions · ?:help"
	}
	if lipgloss.Width(hints) > w-1 && w > 30 {
		hints = "/:commands · ?:help · ctrl+c:quit"
	}
	return statusStyle.Render(hints)
}

// renderPalette returns the list of lines for the slash palette (empty if closed).
func (m model) renderPalette(w int) []string {
	if !m.slashOpen || len(m.slashMatches) == 0 {
		return nil
	}
	maxRows := len(m.slashMatches)
	if maxRows > 6 {
		maxRows = 6
	}
	nameW := 0
	for i := 0; i < maxRows; i++ {
		if n := lipgloss.Width(m.slashMatches[i].name); n > nameW {
			nameW = n
		}
	}
	var out []string
	for i := 0; i < maxRows; i++ {
		c := m.slashMatches[i]
		pad := strings.Repeat(" ", nameW-lipgloss.Width(c.name))
		desc := c.desc
		if c.usage != "" {
			desc += "  " + c.usage
		}
		prefix := "  "
		if i == m.slashCursor {
			prefix = selectedStyle.Render("▶ ")
		}
		name := c.name + pad
		if i == m.slashCursor {
			name = selectedStyle.Render(name)
			desc = bodyStyle.Render(desc)
		} else {
			name = hintStyle.Render(name)
			desc = hintStyle.Render(desc)
		}
		line := prefix + name + "  " + desc
		// Truncate by display width safely
		if lipgloss.Width(line) > w {
			line = truncByDisplay(line, w)
		}
		out = append(out, line)
	}
	return out
}

func (m model) renderInput() string {
	if m.loading {
		spin := m.sp.View()
		label := cancelStyle.Render(spin + " streaming response... esc to cancel")
		return label + "\n" + hintStyle.Render(strings.Repeat("─", m.inputWidth()))
	}
	view := m.input.View()
	return view
}

// ── chat rendering ───────────────────────────────────────────────────────────

func (m model) buildChatLines(w, maxLines int) []string {
	var all []string
	for _, msg := range m.messages {
		var label string
		ts := msg.ts.Format("15:04")
		if msg.role == "user" {
			label = userLabelStyle.Render("you") + "  " + hintStyle.Render(ts)
		} else {
			label = asstLabelStyle.Render("overseer") + "  " + hintStyle.Render(ts)
		}
		all = append(all, label)

		var body string
		if msg.role == "assistant" {
			body = renderMarkdown(msg.content, w-2)
		} else {
			body = msg.content
		}
		if body == "" {
			body = ""
		}
		for _, line := range strings.Split(body, "\n") {
			all = append(all, "  "+line)
		}
		all = append(all, "")
	}
	if m.loading {
		// streaming indicator line at the bottom of the last assistant message
		// (already shown in renderInput)
	}
	if m.lastErr != "" {
		all = append(all, errStyle.Render("error: "+m.lastErr))
		all = append(all, "")
	}

	total := len(all)
	start := total - maxLines - m.scrollOffset
	if start < 0 {
		start = 0
	}
	end := start + maxLines
	if end > total {
		end = total
	}
	visible := append([]string{}, all[start:end]...)
	for len(visible) < maxLines {
		visible = append([]string{""}, visible...)
	}
	return visible
}

// ── activity panel ───────────────────────────────────────────────────────────

func (m model) buildPanelLines(w, h int) []string {
	lines := make([]string, h)
	idx := 0
	set := func(s string) {
		if idx < h {
			lines[idx] = s
			idx++
		}
	}

	set(panelTitleStyle.Render("◇ activity"))
	set(sepStyle.Render(strings.Repeat("─", w)))

	statsH := 3
	entryAreaEnd := h - statsH
	if entryAreaEnd < idx {
		entryAreaEnd = idx
	}

	entries := m.activityLog
	// Show most recent first within reasonable cap
	cap := 0
	for _, e := range entries {
		need := 1
		if e.expanded && (e.preview != "" || e.args != "") {
			need = 4
		} else if e.preview != "" || e.args != "" {
			need = 2
		}
		cap += need
	}
	// Truncate from the front if we'd overflow
	available := entryAreaEnd - idx
	if cap > available {
		// Drop oldest entries until we fit
		newEntries := make([]activityEntry, 0, len(entries))
		used := 0
		for i := len(entries) - 1; i >= 0; i-- {
			e := entries[i]
			need := 1
			if e.expanded && (e.preview != "" || e.args != "") {
				need = 4
			} else if e.preview != "" || e.args != "" {
				need = 2
			}
			if used+need > available {
				break
			}
			newEntries = append([]activityEntry{e}, newEntries...)
			used += need
		}
		entries = newEntries
	}

	for _, e := range entries {
		if idx >= entryAreaEnd {
			break
		}
		var marker string
		switch {
		case e.err:
			marker = activityErrStyle.Render("✗")
		case e.done:
			marker = activityDoneStyle.Render("✓")
		default:
			marker = activityActiveStyle.Render("·")
		}
		name := bodyStyle.Render(trunc(e.name, w-3))
		if !e.done {
			name = activityActiveStyle.Render(trunc(e.name, w-3))
		}
		set(marker + " " + name)

		if e.expanded {
			detail := e.preview
			if detail == "" {
				detail = e.args
			}
			for _, line := range strings.Split(detail, "\n") {
				if idx >= entryAreaEnd {
					break
				}
				set("    " + hintStyle.Render(trunc(line, w-5)))
			}
		} else if e.preview != "" {
			if idx < entryAreaEnd {
				set("    " + hintStyle.Render(trunc(e.preview, w-5)))
			}
		} else if e.args != "" {
			if idx < entryAreaEnd {
				set("    " + hintStyle.Render(trunc(e.args, w-5)))
			}
		}
	}

	// Stats block at the very bottom
	if h >= 3 {
		lines[h-3] = sepStyle.Render(strings.Repeat("─", w))
		stats := ""
		if m.tokensTotal > 0 {
			stats = fmt.Sprintf("%d tok", m.tokensTotal)
			if m.tps > 0 {
				stats += fmt.Sprintf(" · %.0f t/s", m.tps)
			}
		} else {
			stats = "no usage yet"
		}
		lines[h-2] = statsStyle.Render(stats)
		hint := hintStyle.Render("ctrl+e: expand last")
		if lipgloss.Width(hint) > w {
			hint = hintStyle.Render("ctrl+e")
		}
		lines[h-1] = hint
	}
	return lines
}

// ── helpers ───────────────────────────────────────────────────────────────────

func wordWrap(text string, width int) []string {
	if width <= 0 {
		width = 80
	}
	var result []string
	for _, para := range strings.Split(text, "\n") {
		if para == "" {
			result = append(result, "")
			continue
		}
		words := strings.Fields(para)
		var line strings.Builder
		for _, w := range words {
			if line.Len()+len(w)+1 > width && line.Len() > 0 {
				result = append(result, line.String())
				line.Reset()
			}
			if line.Len() > 0 {
				line.WriteByte(' ')
			}
			line.WriteString(w)
		}
		if line.Len() > 0 {
			result = append(result, line.String())
		}
	}
	return result
}

func trunc(s string, max int) string {
	if max < 1 {
		return ""
	}
	if lipgloss.Width(s) <= max {
		return s
	}
	if max <= 1 {
		return "~"
	}
	// Cut by runes, not bytes, to keep UTF-8 intact
	runes := []rune(s)
	for i := len(runes); i > 0; i-- {
		candidate := string(runes[:i]) + "~"
		if lipgloss.Width(candidate) <= max {
			return candidate
		}
	}
	return "~"
}

// stripToolMarkers removes any leaked @@TOOL@@{...} markers from chunked
// content. The server filter handles the common case; this is defence in depth
// for partial markers, unbalanced braces, or model corruption.
var toolMarkerRe = regexp.MustCompile(`@@TOOL@@\{[^\n}]*\}?`)

func stripToolMarkers(s string) string {
	if !strings.Contains(s, "@@TOOL@@") && !strings.Contains(s, "@TOOL@@") {
		return s
	}
	out := toolMarkerRe.ReplaceAllString(s, "")
	// Catch the one-`@` corruption seen in the wild
	out = strings.ReplaceAll(out, "@TOOL@@", "")
	return out
}

// truncByDisplay clips a styled (ANSI-coloured) string by visible width.
// Falls back to byte truncation if the input has no ANSI codes.
func truncByDisplay(s string, max int) string {
	if max < 1 {
		return ""
	}
	if lipgloss.Width(s) <= max {
		return s
	}
	// Walk runes, stripping ANSI escapes from width count
	runes := []rune(s)
	var b strings.Builder
	w := 0
	in := false
	for _, r := range runes {
		if r == 0x1b {
			in = true
			b.WriteRune(r)
			continue
		}
		if in {
			b.WriteRune(r)
			if r == 'm' {
				in = false
			}
			continue
		}
		w++
		if w > max {
			break
		}
		b.WriteRune(r)
	}
	return b.String()
}
