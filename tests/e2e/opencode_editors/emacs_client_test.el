;;; emacs_client_test.el --- Agent Shell ACP lifecycle tests -*- lexical-binding: t; -*-

;; SPDX-License-Identifier: AGPL-3.0-or-later
;; Commercial license available
;; © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
;; © Code 2020–2026 Miroslav Šotek. All rights reserved.
;; ORCID: 0009-0009-3560-0851
;; Contact: www.anulum.li | protoscience@anulum.li
;; SYNAPSE_CHANNEL — behavioural Emacs Agent Shell ACP lifecycle tests

(require 'ert)
(require 'map)

(load
 (expand-file-name "emacs_transport.el" (file-name-directory load-file-name))
 nil nil t)

(defun synapse-e2e-test-state (active pending)
  "Return an Agent Shell state with ACTIVE and PENDING requests."
  `((:client . ((:pending-requests . ,pending)))
    (:active-requests . ,active)))

(ert-deftest synapse-e2e-transport-requires-both-trackers-idle ()
  "Reject either request tracker independently and reject a missing client."
  (with-temp-buffer
    (setq-local agent-shell--state (synapse-e2e-test-state nil nil))
    (should (synapse-e2e-transport-quiescent-p (current-buffer)))
    (setq-local agent-shell--state (synapse-e2e-test-state '(prompt) nil))
    (should-not (synapse-e2e-transport-quiescent-p (current-buffer)))
    (setq-local agent-shell--state (synapse-e2e-test-state nil '((6 . response))))
    (should-not (synapse-e2e-transport-quiescent-p (current-buffer)))
    (setq-local agent-shell--state '((:client . nil) (:active-requests . nil)))
    (should-not (synapse-e2e-transport-quiescent-p (current-buffer)))))

(ert-deftest synapse-e2e-transport-resets-the-stability-window ()
  "Require a continuous idle interval after transient request activity."
  (with-temp-buffer
    (let* ((buffer (current-buffer))
           (now 0.0)
           (waits 0)
           (synapse-e2e-clock-function (lambda () now))
           (synapse-e2e-process-output-function
            (lambda ()
              (setq waits (1+ waits)
                    now (+ now 0.1))
              (cond
               ((= waits 1)
                (with-current-buffer buffer
                  (setq-local
                   agent-shell--state
                   (synapse-e2e-test-state '(session/list) nil))))
               ((= waits 2)
                (with-current-buffer buffer
                  (setq-local
                   agent-shell--state
                   (synapse-e2e-test-state nil nil))))))))
      (setq-local agent-shell--state (synapse-e2e-test-state nil nil))
      (synapse-e2e-wait-for-transport-quiescence buffer 1.0)
      (should (>= now 0.5))
      (should (>= waits 5)))))

(ert-deftest synapse-e2e-transport-resamples-after-output-service ()
  "Reset stability when the threshold-crossing service starts a request."
  (with-temp-buffer
    (let* ((buffer (current-buffer))
           (now 0.0)
           (waits 0)
           (synapse-e2e-clock-function (lambda () now))
           (synapse-e2e-process-output-function
            (lambda ()
              (setq waits (1+ waits)
                    now (+ now 0.1))
              (cond
               ((= waits 3)
                (with-current-buffer buffer
                  (setq-local
                   agent-shell--state
                   (synapse-e2e-test-state '(session/list) nil))))
               ((= waits 4)
                (with-current-buffer buffer
                  (setq-local
                   agent-shell--state
                   (synapse-e2e-test-state nil nil))))))))
      (setq-local agent-shell--state (synapse-e2e-test-state nil nil))
      (synapse-e2e-wait-for-transport-quiescence buffer 1.0)
      (should (> now 0.6))
      (should (>= waits 7)))))

(ert-deftest synapse-e2e-transport-times-out-while-active ()
  "Fail closed when either request tracker stays busy through the deadline."
  (with-temp-buffer
    (let* ((buffer (current-buffer))
           (now 0.0)
           (synapse-e2e-clock-function (lambda () now))
           (synapse-e2e-process-output-function
            (lambda () (setq now (+ now 0.1)))))
      (setq-local
       agent-shell--state
       (synapse-e2e-test-state nil '((6 . response))))
      (should-error
       (synapse-e2e-wait-for-transport-quiescence buffer 0.3)
       :type 'error)
      (should (>= now 0.3)))))

;;; emacs_client_test.el ends here
