//go:build windows

package services

import (
	"fmt"
	"os/exec"
)

func configurePythonProcess(cmd *exec.Cmd) {}

func killPythonProcess(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = exec.Command("taskkill", "/PID", fmt.Sprint(cmd.Process.Pid), "/T", "/F").Run()
	}
}
