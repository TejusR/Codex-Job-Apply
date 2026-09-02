package main

import (
	"os"

	"github.com/ramesh/codex-job-apply/internal/cli"
)

func main() { os.Exit(cli.Execute(os.Args[1:])) }
