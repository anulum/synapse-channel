;;; emacs_transport.el --- Agent Shell ACP transport lifecycle -*- lexical-binding: t; -*-

;; SPDX-License-Identifier: AGPL-3.0-or-later
;; Commercial license available
;; © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
;; © Code 2020–2026 Miroslav Šotek. All rights reserved.
;; ORCID: 0009-0009-3560-0851
;; Contact: www.anulum.li | protoscience@anulum.li
;; SYNAPSE_CHANNEL — bounded Emacs Agent Shell ACP transport lifecycle

(require 'map)

(defvar agent-shell--state)

(defconst synapse-e2e-quiescence-seconds 0.25
  "Continuous idle interval required before the ACP client is torn down.")

(defvar synapse-e2e-clock-function #'float-time
  "Function returning the clock used by transport waits.")

(defvar synapse-e2e-process-output-function
  (lambda () (accept-process-output nil 0.05))
  "Function servicing process output once during a transport wait.")

(defun synapse-e2e-transport-quiescent-p (buffer)
  "Return non-nil when BUFFER has no Agent Shell or ACP request in flight."
  (with-current-buffer buffer
    (let ((client (map-elt agent-shell--state :client)))
      (and client
           (null (map-elt agent-shell--state :active-requests))
           (null (map-elt client :pending-requests))))))

(defun synapse-e2e-wait-for-transport-quiescence (buffer timeout)
  "Wait for BUFFER's ACP transport to stay idle for a bounded TIMEOUT."
  (let ((deadline (+ (funcall synapse-e2e-clock-function) timeout))
        (stable-since nil))
    (while
        (and (< (funcall synapse-e2e-clock-function) deadline)
             (or (not stable-since)
                 (< (- (funcall synapse-e2e-clock-function) stable-since)
                    synapse-e2e-quiescence-seconds)))
      (if (synapse-e2e-transport-quiescent-p buffer)
          (unless stable-since
            (setq stable-since (funcall synapse-e2e-clock-function)))
        (setq stable-since nil))
      (funcall synapse-e2e-process-output-function)
      (unless (synapse-e2e-transport-quiescent-p buffer)
        (setq stable-since nil)))
    (unless
        (and stable-since
             (>= (- (funcall synapse-e2e-clock-function) stable-since)
                 synapse-e2e-quiescence-seconds)
             (synapse-e2e-transport-quiescent-p buffer))
      (error "Agent Shell ACP transport did not become quiescent"))))

(provide 'synapse-e2e-emacs-transport)

;;; emacs_transport.el ends here
