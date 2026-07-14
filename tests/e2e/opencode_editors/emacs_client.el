;;; emacs_client.el --- Real Agent Shell ACP acceptance client -*- lexical-binding: t; -*-

;; SPDX-License-Identifier: AGPL-3.0-or-later
;; Commercial license available
;; © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
;; © Code 2020–2026 Miroslav Šotek. All rights reserved.
;; ORCID: 0009-0009-3560-0851
;; Contact: www.anulum.li | protoscience@anulum.li
;; SYNAPSE_CHANNEL — real Emacs Agent Shell ACP acceptance client

(require 'json)
(require 'seq)
(require 'subr-x)
(require 'agent-shell)
(require 'agent-shell-opencode)

(defun synapse-e2e-required-env (name)
  "Return non-empty environment variable NAME or signal an error."
  (let ((value (getenv name)))
    (unless (and value (not (string-empty-p value)))
      (error "%s is required" name))
    value))

(defun synapse-e2e-wait-until (predicate timeout message)
  "Wait for PREDICATE up to TIMEOUT seconds, then signal MESSAGE."
  (let ((deadline (+ (float-time) timeout)))
    (while (and (not (funcall predicate)) (< (float-time) deadline))
      (accept-process-output nil 0.05))
    (unless (funcall predicate)
      (error "%s" message))))

(let* ((argv (json-parse-string
              (synapse-e2e-required-env "SYNAPSE_ACP_PROXY_ARGV_JSON")
              :array-type 'list))
       (agent-shell-opencode-acp-command argv)
       (agent-shell-show-welcome-message nil)
       (agent-shell-show-busy-indicator nil)
       (agent-shell-header-style nil)
       (agent-shell-session-strategy 'new)
       (buffer (agent-shell-start
                :config (agent-shell-opencode-make-agent-config))))
  (unless (buffer-live-p buffer)
    (error "Agent Shell did not create a live buffer"))
  (synapse-e2e-wait-until
   (lambda ()
     (with-current-buffer buffer
       (and (map-nested-elt agent-shell--state '(:session :id))
            (not (shell-maker-busy)))))
   60
   "Agent Shell failed to initialize OpenCode ACP")
  (with-current-buffer buffer
    (agent-shell-queue-request
     (synapse-e2e-required-env "SYNAPSE_EDITOR_E2E_PROMPT")))
  (synapse-e2e-wait-until
   (lambda ()
     (with-current-buffer buffer
       (and (not (shell-maker-busy))
            (string-match-p
             (regexp-quote
              (synapse-e2e-required-env "SYNAPSE_EDITOR_E2E_RESPONSE"))
             (buffer-substring-no-properties (point-min) (point-max))))))
   60
   "Agent Shell did not render the deterministic response")
  (kill-buffer buffer))

;;; emacs_client.el ends here
