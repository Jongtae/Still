# Written by scripts/dev/release.py formula from deploy/homebrew/agentos.rb.template
# for the published tag archive. Change the template, not this file.

class Agentos < Formula
  desc "Self-hosted personal agent with browser setup and Telegram"
  homepage "https://github.com/Jongtae/Still"
  url "https://github.com/Jongtae/Still/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "dabb1c5235651f9a30962bedecc6ef8f0acdf0077e9c0c23a51b120bd5cba257"
  version_scheme 1
  depends_on "python@3.13"

  def install
    system Formula["python@3.13"].opt_bin/"python3.13", "-m", "venv", libexec
    system libexec/"bin/pip", "install", "#{buildpath}[mcp-host]"
    (bin/"agentos").write <<~PYTHON
      #!#{libexec}/bin/python
      from personal_agent.quickstart import main
      main()
    PYTHON
  end

  def caveats
    <<~EOS
      Run agentos start to open browser setup.
      Data: ~/.local/share/agentos
      To start Still at login and after a restart, without a terminal (macOS):
        #{opt_bin}/agentos service install --cli-path #{opt_bin}/agentos
      Otherwise keep agentos start running to receive Telegram requests.
    EOS
  end

  test do
    assert_match "personal agent", shell_output("#{bin}/agentos --help")
    system libexec/"bin/python", "-c", "from mcp.server.stdio import stdio_server"
  end
end
