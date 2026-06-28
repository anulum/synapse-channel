// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// Copyright Concepts 1996-2026 Miroslav Sotek. All rights reserved.
// Copyright Code 2020-2026 Miroslav Sotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL - read-only Go client for ops and CI tools

// Package synapse provides a small read-only HTTP client for SYNAPSE CHANNEL
// operator and CI tooling.
//
// The package intentionally targets HTTP JSON surfaces such as
// "synapse dashboard" /snapshot.json. It does not implement the WebSocket
// mutation protocol for claims, chat, or board writes.
package synapse

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const maxErrorBodyBytes int64 = 4096

// HTTPDoer is the subset of http.Client used by Client.
type HTTPDoer interface {
	Do(req *http.Request) (*http.Response, error)
}

// Option configures a Client.
type Option func(*Client)

// Client is a read-only HTTP client for local SYNAPSE ops surfaces.
type Client struct {
	baseURL    *url.URL
	httpClient HTTPDoer
	bearer     string
}

// DashboardSnapshot is the stable JSON shape returned by /snapshot.json.
type DashboardSnapshot struct {
	OnlineAgents []string         `json:"online_agents"`
	State        map[string]any   `json:"state"`
	Board        map[string]any   `json:"board"`
	Manifest     []map[string]any `json:"manifest"`
	Fleet        map[string]any   `json:"fleet"`
}

// StatusError reports a non-2xx HTTP response from a SYNAPSE endpoint.
type StatusError struct {
	StatusCode int
	Status     string
	Body       string
}

// Error returns the formatted HTTP status and bounded response body.
func (err *StatusError) Error() string {
	body := strings.TrimSpace(err.Body)
	if body == "" {
		return fmt.Sprintf("synapse: HTTP %s", err.Status)
	}
	return fmt.Sprintf("synapse: HTTP %s: %s", err.Status, body)
}

// WithHTTPClient replaces the HTTP client used for requests.
func WithHTTPClient(httpClient HTTPDoer) Option {
	return func(client *Client) {
		if httpClient != nil {
			client.httpClient = httpClient
		}
	}
}

// WithBearerToken sends token as an Authorization bearer token.
func WithBearerToken(token string) Option {
	return func(client *Client) {
		client.bearer = strings.TrimSpace(token)
	}
}

// NewClient creates a read-only client rooted at baseURL.
func NewClient(baseURL string, options ...Option) (*Client, error) {
	trimmed := strings.TrimSpace(baseURL)
	if trimmed == "" {
		return nil, errors.New("synapse: base URL is required")
	}
	parsed, err := url.Parse(trimmed)
	if err != nil {
		return nil, fmt.Errorf("synapse: parse base URL: %w", err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, fmt.Errorf("synapse: unsupported base URL scheme %q", parsed.Scheme)
	}
	if parsed.Host == "" {
		return nil, errors.New("synapse: base URL host is required")
	}
	client := &Client{
		baseURL: parsed,
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
	for _, option := range options {
		if option != nil {
			option(client)
		}
	}
	return client, nil
}

// DashboardSnapshot fetches and decodes /snapshot.json from synapse dashboard.
func (client *Client) DashboardSnapshot(ctx context.Context) (*DashboardSnapshot, error) {
	var snapshot DashboardSnapshot
	if err := client.GetJSON(ctx, "/snapshot.json", &snapshot); err != nil {
		return nil, err
	}
	return &snapshot, nil
}

// GetJSON issues a GET request for path and decodes a JSON response into target.
func (client *Client) GetJSON(ctx context.Context, path string, target any) error {
	if target == nil {
		return errors.New("synapse: JSON target is required")
	}
	endpoint, err := client.endpoint(path)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return fmt.Errorf("synapse: build request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	if client.bearer != "" {
		req.Header.Set("Authorization", "Bearer "+client.bearer)
	}
	resp, err := client.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("synapse: GET %s: %w", endpoint, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, maxErrorBodyBytes))
		if readErr != nil {
			return fmt.Errorf("synapse: read error response: %w", readErr)
		}
		return &StatusError{
			StatusCode: resp.StatusCode,
			Status:     resp.Status,
			Body:       string(body),
		}
	}
	decoder := json.NewDecoder(resp.Body)
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("synapse: decode JSON response: %w", err)
	}
	return nil
}

func (client *Client) endpoint(path string) (string, error) {
	trimmed := strings.TrimSpace(path)
	if trimmed == "" {
		return "", errors.New("synapse: request path is required")
	}
	if strings.Contains(trimmed, "://") || strings.HasPrefix(trimmed, "//") {
		return "", errors.New("synapse: request path must be relative")
	}
	if !strings.HasPrefix(trimmed, "/") {
		trimmed = "/" + trimmed
	}
	next := *client.baseURL
	next.Path = strings.TrimRight(client.baseURL.Path, "/") + trimmed
	next.RawQuery = ""
	next.Fragment = ""
	return next.String(), nil
}
