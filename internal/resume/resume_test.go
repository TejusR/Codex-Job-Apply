package resume

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeTemplate(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "resume-template.tex")
	content := `\documentclass{article}
\begin{document}
\section*{Summary}
\begin{itemize}
% BEGIN AUTO_SUMMARY
\item Original summary
% END AUTO_SUMMARY
\end{itemize}
\section*{Skills}
\begin{itemize}
% BEGIN AUTO_SKILLS
\item Original skills
% END AUTO_SKILLS
\end{itemize}
\section*{Experience}
Company and dates stay immutable.
\begin{itemize}
% BEGIN AUTO_BULLETS:experience
\item Original bullet
% END AUTO_BULLETS:experience
\end{itemize}
\end{document}
`
	os.WriteFile(path, []byte(content), 0o600)
	return path
}
func TestParseAndRenderTemplate(t *testing.T) {
	template, err := ParseTemplate(writeTemplate(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(template.Blocks) != 3 || len(template.BulletSlugs()) != 1 || template.BulletSlugs()[0] != "experience" {
		t.Fatalf("unexpected template: %#v", template)
	}
	payload := Customization{Summary: []string{"Tailored summary"}, Skills: []string{"Programming: Java, Python"}, BulletBlocks: []BulletBlock{{Slug: "experience", Bullets: []string{"Built resilient backend systems."}}}}
	if err = Validate(payload, template); err != nil {
		t.Fatal(err)
	}
	rendered := Render(template, payload)
	for _, want := range []string{`\item Tailored summary`, `\item Programming: Java, Python`, `\item Built resilient backend systems.`, `Company and dates stay immutable.`} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("missing %q in %s", want, rendered)
		}
	}
}
func TestRejectUnknownBulletSlug(t *testing.T) {
	template, _ := ParseTemplate(writeTemplate(t))
	err := Validate(Customization{Summary: []string{"Summary"}, Skills: []string{"Skills"}, BulletBlocks: []BulletBlock{{Slug: "unknown", Bullets: []string{"Bullet"}}}}, template)
	if err == nil || !strings.Contains(strings.ToLower(err.Error()), "unknown bullet block slug") {
		t.Fatalf("unexpected error: %v", err)
	}
}
