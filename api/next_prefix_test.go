package main

import "testing"

func TestStripNextPrefix(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"root", "/", "/"},
		{"frontend route", "/preview", "/preview"},
		{"unversioned", "/_next/static/chunks/a.css", "/_next/static/chunks/a.css"},
		{"single prefix, static", "/_next-1792252346260/static/chunks/a.css", "/_next/static/chunks/a.css"},
		{"single prefix, nested _next", "/_next-1792252346260/_next/static/chunks/a.css", "/_next/static/chunks/a.css"},
		{"stacked prefix (v0.10.8 bug)", "/_next-1/_next-2/static/chunks/a.css", "/_next/static/chunks/a.css"},
		{"stacked prefix with nested _next", "/_next-1/_next-2/_next/static/chunks/a.css", "/_next/static/chunks/a.css"},
		{"prefix without path", "/_next-123", "/_next-123"},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := stripNextPrefix(c.in); got != c.want {
				t.Fatalf("stripNextPrefix(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}
