-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Commercial license available
-- © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
-- © Code 2020–2026 Miroslav Šotek. All rights reserved.
-- ORCID: 0009-0009-3560-0851
-- Contact: www.anulum.li | protoscience@anulum.li
-- SYNAPSE_CHANNEL — real CodeCompanion.nvim ACP acceptance client

local function required_env(name)
  local value = vim.env[name]
  assert(value and value ~= "", name .. " is required")
  return value
end

vim.opt.runtimepath:append(required_env("SYNAPSE_CODECOMPANION_DIR"))
vim.opt.runtimepath:append(required_env("SYNAPSE_PLENARY_DIR"))

local proxy_argv = vim.json.decode(required_env("SYNAPSE_ACP_PROXY_ARGV_JSON"))
assert(type(proxy_argv) == "table" and #proxy_argv > 0, "invalid ACP proxy argv")

local adapter = vim.deepcopy(require("codecompanion.adapters.acp.opencode"))
adapter.commands.default = proxy_argv
adapter.defaults.timeout = 60000

local connection = require("codecompanion.acp").new({ adapter = adapter })
assert(connection:connect_and_initialize(), "CodeCompanion failed to initialize OpenCode ACP")

local finished = false
local failure = nil
local response = {}
connection
  :session_prompt({
    {
      role = "user",
      content = required_env("SYNAPSE_EDITOR_E2E_PROMPT"),
      _meta = {},
    },
  })
  :on_message_chunk(function(content)
    table.insert(response, content)
  end)
  :on_complete(function()
    finished = true
  end)
  :on_error(function(message)
    failure = message
    finished = true
  end)
  :send()

assert(vim.wait(60000, function()
  return finished
end, 20), "CodeCompanion ACP prompt timed out")
assert(not failure, "CodeCompanion ACP prompt failed: " .. tostring(failure))
assert(
  table.concat(response):find(required_env("SYNAPSE_EDITOR_E2E_RESPONSE"), 1, true),
  "CodeCompanion did not render the deterministic response"
)
connection:disconnect()
