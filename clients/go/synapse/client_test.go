// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// Copyright Concepts 1996-2026 Miroslav Sotek. All rights reserved.
// Copyright Code 2020-2026 Miroslav Sotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL - tests for the read-only Go ops client

package synapse

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) Do(req *http.Request) (*http.Response, error) {
	return fn(req)
}

type errorReader struct{}

func (errorReader) Read(_ []byte) (int, error) {
	return 0, errors.New("read failed")
}

func (errorReader) Close() error {
	return nil
}

func TestNewClientRejectsEmptyOrUnsupportedBaseURL(t *testing.T) {
	t.Parallel()

	if _, err := NewClient(""); err == nil {
		t.Fatal("NewClient accepted an empty base URL")
	}
	if _, err := NewClient("%"); err == nil {
		t.Fatal("NewClient accepted an unparsable base URL")
	}
	if _, err := NewClient("ftp://127.0.0.1:8765"); err == nil {
		t.Fatal("NewClient accepted an unsupported scheme")
	}
	if _, err := NewClient("http:///missing-host"); err == nil {
		t.Fatal("NewClient accepted a URL without a host")
	}
}

func TestGetJSONReportsRequestBuildFailure(t *testing.T) {
	t.Parallel()

	client := &Client{
		baseURL: &url.URL{Scheme: "http\n", Host: "127.0.0.1:8765"},
		httpClient: roundTripFunc(func(_ *http.Request) (*http.Response, error) {
			t.Fatal("request should fail before transport")
			return nil, nil
		}),
	}
	var payload map[string]any
	if err := client.GetJSON(context.Background(), "/snapshot.json", &payload); err == nil {
		t.Fatal("GetJSON ignored request build failure")
	}
}

func TestDashboardSnapshotSendsBearerAndDecodesPayload(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/snapshot.json" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer secret" {
			t.Fatalf("unexpected authorization header: %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"online_agents": ["ci"],
			"state": {"claims": []},
			"board": {"tasks": []},
			"manifest": [{"name": "builder"}],
			"fleet": {"claims": {"active": 0}}
		}`))
	}))
	defer server.Close()

	client, err := NewClient(server.URL, WithBearerToken("secret"))
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}

	snapshot, err := client.DashboardSnapshot(context.Background())
	if err != nil {
		t.Fatalf("DashboardSnapshot failed: %v", err)
	}

	if len(snapshot.OnlineAgents) != 1 || snapshot.OnlineAgents[0] != "ci" {
		t.Fatalf("unexpected online agents: %#v", snapshot.OnlineAgents)
	}
	if len(snapshot.Manifest) != 1 || snapshot.Manifest[0]["name"] != "builder" {
		t.Fatalf("unexpected manifest: %#v", snapshot.Manifest)
	}
	if snapshot.Fleet["claims"] == nil {
		t.Fatalf("expected fleet claims in snapshot: %#v", snapshot.Fleet)
	}
}

func TestGetJSONUsesCustomHTTPClientAndRelativePaths(t *testing.T) {
	t.Parallel()

	client, err := NewClient(
		"http://127.0.0.1:8765/base/",
		WithHTTPClient(roundTripFunc(func(req *http.Request) (*http.Response, error) {
			if req.URL.String() != "http://127.0.0.1:8765/base/snapshot.json" {
				t.Fatalf("unexpected URL: %s", req.URL.String())
			}
			if got := req.Header.Get("Accept"); got != "application/json" {
				t.Fatalf("unexpected accept header: %q", got)
			}
			return &http.Response{
				StatusCode: http.StatusOK,
				Status:     "200 OK",
				Body:       io.NopCloser(strings.NewReader(`{"ok": true}`)),
			}, nil
		})),
		WithHTTPClient(nil),
	)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}

	var payload map[string]bool
	if err := client.GetJSON(context.Background(), "snapshot.json", &payload); err != nil {
		t.Fatalf("GetJSON failed: %v", err)
	}
	if !payload["ok"] {
		t.Fatalf("unexpected payload: %#v", payload)
	}
}

func TestStatusErrorFormatsStatusAndOptionalBody(t *testing.T) {
	t.Parallel()

	withBody := (&StatusError{Status: "503 Service Unavailable", Body: " unavailable \n"}).Error()
	if !strings.Contains(withBody, "503 Service Unavailable: unavailable") {
		t.Fatalf("unexpected status error with body: %q", withBody)
	}

	withoutBody := (&StatusError{Status: "404 Not Found"}).Error()
	if withoutBody != "synapse: HTTP 404 Not Found" {
		t.Fatalf("unexpected status error without body: %q", withoutBody)
	}
}

func TestGetJSONReportsHTTPErrorStatusAndBody(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "dashboard unavailable", http.StatusServiceUnavailable)
	}))
	defer server.Close()

	client, err := NewClient(server.URL)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}

	var payload map[string]any
	err = client.GetJSON(context.Background(), "/snapshot.json", &payload)
	if err == nil {
		t.Fatal("GetJSON returned nil for an HTTP error")
	}
	var statusErr *StatusError
	if !errors.As(err, &statusErr) {
		t.Fatalf("expected StatusError, got %T", err)
	}
	if statusErr.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("unexpected status code: %d", statusErr.StatusCode)
	}
	if !strings.Contains(statusErr.Body, "dashboard unavailable") {
		t.Fatalf("unexpected error body: %q", statusErr.Body)
	}
}

func TestGetJSONRejectsNilTarget(t *testing.T) {
	t.Parallel()

	client, err := NewClient("http://127.0.0.1:8765")
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}

	if err := client.GetJSON(context.Background(), "/snapshot.json", nil); err == nil {
		t.Fatal("GetJSON accepted a nil target")
	}
}

func TestGetJSONRejectsInvalidPaths(t *testing.T) {
	t.Parallel()

	client, err := NewClient("http://127.0.0.1:8765")
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}

	var payload map[string]any
	for _, path := range []string{"", "https://example.test/snapshot.json", "//example.test/x"} {
		if err := client.GetJSON(context.Background(), path, &payload); err == nil {
			t.Fatalf("GetJSON accepted invalid path %q", path)
		}
	}
}

func TestGetJSONReportsTransportDecodeAndErrorBodyFailures(t *testing.T) {
	t.Parallel()

	transportClient, err := NewClient(
		"http://127.0.0.1:8765",
		WithHTTPClient(roundTripFunc(func(_ *http.Request) (*http.Response, error) {
			return nil, errors.New("dial failed")
		})),
	)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	var payload map[string]any
	if err := transportClient.GetJSON(context.Background(), "/snapshot.json", &payload); err == nil {
		t.Fatal("GetJSON ignored a transport error")
	}

	decodeClient, err := NewClient(
		"http://127.0.0.1:8765",
		WithHTTPClient(roundTripFunc(func(_ *http.Request) (*http.Response, error) {
			return &http.Response{
				StatusCode: http.StatusOK,
				Status:     "200 OK",
				Body:       io.NopCloser(strings.NewReader("{")),
			}, nil
		})),
	)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	if err := decodeClient.GetJSON(context.Background(), "/snapshot.json", &payload); err == nil {
		t.Fatal("GetJSON ignored invalid JSON")
	}

	bodyClient, err := NewClient(
		"http://127.0.0.1:8765",
		WithHTTPClient(roundTripFunc(func(_ *http.Request) (*http.Response, error) {
			return &http.Response{
				StatusCode: http.StatusBadGateway,
				Status:     "502 Bad Gateway",
				Body:       errorReader{},
			}, nil
		})),
	)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	if err := bodyClient.GetJSON(context.Background(), "/snapshot.json", &payload); err == nil {
		t.Fatal("GetJSON ignored an error-body read failure")
	}
}

func TestDashboardSnapshotReturnsFetchError(t *testing.T) {
	t.Parallel()

	client, err := NewClient(
		"http://127.0.0.1:8765",
		WithHTTPClient(roundTripFunc(func(_ *http.Request) (*http.Response, error) {
			return &http.Response{
				StatusCode: http.StatusInternalServerError,
				Status:     "500 Internal Server Error",
				Body:       io.NopCloser(strings.NewReader("not ready")),
			}, nil
		})),
	)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}

	if _, err := client.DashboardSnapshot(context.Background()); err == nil {
		t.Fatal("DashboardSnapshot ignored fetch failure")
	}
}
