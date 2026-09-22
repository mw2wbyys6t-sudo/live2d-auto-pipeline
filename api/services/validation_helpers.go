package services

import (
	"fmt"
	"regexp"
)

var characterIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func validateCharacterID(id string) error {
	if !characterIDPattern.MatchString(id) {
		return fmt.Errorf("角色编号无效")
	}
	return nil
}
