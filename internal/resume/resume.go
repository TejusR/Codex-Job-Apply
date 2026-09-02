package resume

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

var beginBlock = regexp.MustCompile(`^\s*% BEGIN AUTO_(SUMMARY|SKILLS|BULLETS:([A-Za-z0-9_-]+))\s*$`)
var endBlock = regexp.MustCompile(`^\s*% END AUTO_(SUMMARY|SKILLS|BULLETS:([A-Za-z0-9_-]+))\s*$`)

type Block struct {
	Type            string
	Slug            string
	ContentStart    int
	ContentEnd      int
	OriginalContent string
}

type Template struct {
	Path, Text string
	Blocks     []Block
}

type BulletBlock struct {
	Slug    string   `json:"slug"`
	Bullets []string `json:"bullets"`
}
type Customization struct {
	Summary      []string      `json:"summary"`
	Skills       []string      `json:"skills"`
	BulletBlocks []BulletBlock `json:"bullet_blocks"`
}

func (t Template) BulletSlugs() []string {
	out := []string{}
	for _, block := range t.Blocks {
		if block.Type == "bullets" && block.Slug != "" {
			out = append(out, block.Slug)
		}
	}
	return out
}

func ParseTemplate(path string) (Template, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Template{}, err
	}
	text := string(data)
	lines := splitLinesKeepEnds(text)
	offsets := []int{0}
	for _, line := range lines {
		offsets = append(offsets, offsets[len(offsets)-1]+len(line))
	}
	blocks := []Block{}
	for i := 0; i < len(lines); i++ {
		begin := beginBlock.FindStringSubmatch(strings.TrimRight(lines[i], "\r\n"))
		if begin == nil {
			continue
		}
		endIndex := i + 1
		for ; endIndex < len(lines); endIndex++ {
			end := endBlock.FindStringSubmatch(strings.TrimRight(lines[endIndex], "\r\n"))
			if end == nil {
				continue
			}
			if end[1] != begin[1] || end[2] != begin[2] {
				return Template{}, fmt.Errorf("mismatched template block markers in %s: BEGIN %s / END %s", path, begin[1], end[1])
			}
			break
		}
		if endIndex >= len(lines) {
			return Template{}, fmt.Errorf("unterminated template block marker in %s: %s", path, strings.TrimSpace(lines[i]))
		}
		kind := strings.ToLower(begin[1])
		if strings.HasPrefix(begin[1], "BULLETS:") {
			kind = "bullets"
		}
		blocks = append(blocks, Block{Type: kind, Slug: begin[2], ContentStart: offsets[i+1], ContentEnd: offsets[endIndex], OriginalContent: text[offsets[i+1]:offsets[endIndex]]})
		i = endIndex
	}
	hasSummary, hasSkills, hasBullets := false, false, false
	for _, block := range blocks {
		hasSummary = hasSummary || block.Type == "summary"
		hasSkills = hasSkills || block.Type == "skills"
		hasBullets = hasBullets || block.Type == "bullets"
	}
	if !hasSummary {
		return Template{}, fmt.Errorf("template %s is missing %% BEGIN AUTO_SUMMARY", path)
	}
	if !hasSkills {
		return Template{}, fmt.Errorf("template %s is missing %% BEGIN AUTO_SKILLS", path)
	}
	if !hasBullets {
		return Template{}, fmt.Errorf("template %s is missing at least one %% BEGIN AUTO_BULLETS:<slug> block", path)
	}
	return Template{Path: path, Text: text, Blocks: blocks}, nil
}

func Validate(payload Customization, template Template) error {
	if len(payload.Summary) == 0 {
		return errors.New("field 'summary' must be a non-empty list")
	}
	if len(payload.Skills) == 0 {
		return errors.New("field 'skills' must be a non-empty list")
	}
	for _, value := range append(append([]string{}, payload.Summary...), payload.Skills...) {
		if strings.TrimSpace(value) == "" {
			return errors.New("summary and skill items must be non-empty strings")
		}
	}
	allowed := map[string]bool{}
	for _, slug := range template.BulletSlugs() {
		allowed[slug] = true
	}
	seen := map[string]bool{}
	for _, block := range payload.BulletBlocks {
		if strings.TrimSpace(block.Slug) == "" {
			return errors.New("bullet blocks must include a non-empty 'slug'")
		}
		if !allowed[block.Slug] {
			return fmt.Errorf("unknown bullet block slug: %s", block.Slug)
		}
		if seen[block.Slug] {
			return fmt.Errorf("duplicate bullet block slug: %s", block.Slug)
		}
		seen[block.Slug] = true
		if len(block.Bullets) == 0 {
			return fmt.Errorf("bullet block '%s' must include a non-empty bullets list", block.Slug)
		}
		for _, bullet := range block.Bullets {
			if strings.TrimSpace(bullet) == "" {
				return fmt.Errorf("bullet block '%s' contains an empty bullet", block.Slug)
			}
		}
	}
	return nil
}

func Render(template Template, payload Customization) string {
	bulletMap := map[string][]string{}
	for _, block := range payload.BulletBlocks {
		bulletMap[block.Slug] = block.Bullets
	}
	var out strings.Builder
	cursor := 0
	for _, block := range template.Blocks {
		out.WriteString(template.Text[cursor:block.ContentStart])
		switch block.Type {
		case "summary":
			out.WriteString(renderItems(payload.Summary))
		case "skills":
			out.WriteString(renderItems(payload.Skills))
		case "bullets":
			items := bulletMap[block.Slug]
			if len(items) == 0 {
				items = extractBullets(block.OriginalContent)
			}
			out.WriteString(renderItems(items))
		default:
			out.WriteString(block.OriginalContent)
		}
		cursor = block.ContentEnd
	}
	out.WriteString(template.Text[cursor:])
	return out.String()
}

func Preview(payload Customization, title, company string) string {
	lines := []string{}
	bits := []string{}
	if title != "" {
		bits = append(bits, title)
	}
	if company != "" {
		bits = append(bits, company)
	}
	if len(bits) > 0 {
		lines = append(lines, "# Tailored Resume Preview: "+strings.Join(bits, " - "), "")
	}
	lines = append(lines, "## Summary")
	for _, value := range payload.Summary {
		lines = append(lines, "- "+value)
	}
	lines = append(lines, "", "## Skills")
	for _, value := range payload.Skills {
		lines = append(lines, "- "+value)
	}
	lines = append(lines, "", "## Tailored Experience Highlights")
	for _, block := range payload.BulletBlocks {
		lines = append(lines, "### "+block.Slug)
		for _, bullet := range block.Bullets {
			lines = append(lines, "- "+bullet)
		}
		lines = append(lines, "")
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

func Compile(ctx context.Context, texPath, outputDir string) (string, string, error) {
	if latexmk, err := exec.LookPath("latexmk"); err == nil {
		args := []string{"-pdf", "-interaction=nonstopmode", "-halt-on-error", "-output-directory=" + outputDir, texPath}
		if output, err := run(ctx, latexmk, args, outputDir); err != nil {
			return "", "", fmt.Errorf("latexmk failed: %w: %s", err, output)
		}
		pdf := filepath.Join(outputDir, strings.TrimSuffix(filepath.Base(texPath), filepath.Ext(texPath))+".pdf")
		if fileExists(pdf) {
			return pdf, "latexmk", nil
		}
	}
	pdflatex, err := exec.LookPath("pdflatex")
	if err != nil {
		return "", "", errors.New("no LaTeX compiler was found in PATH")
	}
	args := []string{"-interaction=nonstopmode", "-halt-on-error", "-output-directory=" + outputDir, texPath}
	for i := 0; i < 2; i++ {
		if output, err := run(ctx, pdflatex, args, outputDir); err != nil {
			return "", "", fmt.Errorf("pdflatex failed: %w: %s", err, output)
		}
	}
	pdf := filepath.Join(outputDir, strings.TrimSuffix(filepath.Base(texPath), filepath.Ext(texPath))+".pdf")
	if !fileExists(pdf) {
		return "", "", fmt.Errorf("LaTeX compilation finished without producing %s", filepath.Base(pdf))
	}
	return pdf, "pdflatex", nil
}

func DescriptionHash(text string) string {
	digest := sha256.Sum256([]byte(strings.TrimSpace(text)))
	return hex.EncodeToString(digest[:])
}

func EscapeLatex(text string) string {
	replacements := map[rune]string{'\\': `\textbackslash{}`, '&': `\&`, '%': `\%`, '$': `\$`, '#': `\#`, '_': `\_`, '{': `\{`, '}': `\}`, '~': `\textasciitilde{}`, '^': `\textasciicircum{}`}
	var out strings.Builder
	for _, char := range text {
		if value, ok := replacements[char]; ok {
			out.WriteString(value)
		} else {
			out.WriteRune(char)
		}
	}
	return strings.NewReplacer("–", "--", "—", "---", "→", `$\rightarrow$`).Replace(out.String())
}

func splitLinesKeepEnds(text string) []string {
	if text == "" {
		return []string{}
	}
	lines := []string{}
	for len(text) > 0 {
		i := strings.IndexByte(text, '\n')
		if i < 0 {
			lines = append(lines, text)
			break
		}
		lines = append(lines, text[:i+1])
		text = text[i+1:]
	}
	return lines
}
func renderItems(items []string) string {
	var out strings.Builder
	for _, item := range items {
		if value := strings.TrimSpace(item); value != "" {
			out.WriteString("\\item " + EscapeLatex(value) + "\n")
		}
	}
	return out.String()
}
func extractBullets(text string) []string {
	out := []string{}
	for _, line := range strings.Split(text, "\n") {
		value := strings.TrimSpace(line)
		if strings.HasPrefix(value, `\item `) {
			out = append(out, strings.TrimSpace(strings.TrimPrefix(value, `\item `)))
		}
	}
	return out
}
func run(ctx context.Context, name string, args []string, cwd string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Dir = cwd
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}
func fileExists(path string) bool { info, err := os.Stat(path); return err == nil && !info.IsDir() }

// CommandName returns a directly executable command on Unix and preserves the
// original filename on Windows, where the caller may wrap .cmd/.bat with cmd.exe.
func CommandName(path string) string {
	if runtime.GOOS == "windows" {
		return path
	}
	return path
}
