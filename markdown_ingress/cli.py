#!/usr/bin/env python3
"""
MarkDownIngress CLI
"""

import sys
import json
import argparse
from pathlib import Path
from markdown_ingress import ingest, __version__
from markdown_ingress.core.scoring import Scorer


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="MarkDownIngress - Deterministic, Injection-Resistant Web → Markdown Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  markdown-ingress https://example.com
  markdown-ingress https://example.com --strict --model gpt-4
  markdown-ingress https://example.com --json --save output.json
  markdown-ingress https://example.com --save output.md
        """
    )
    
    parser.add_argument('url', help='URL to ingest')
    parser.add_argument('--render', action='store_true', help='Use render mode (Playwright, not yet implemented)')
    parser.add_argument('--strict', action='store_true', default=True, help='Enable strict security mode (default: true)')
    parser.add_argument('--permissive', action='store_true', help='Disable strict mode')
    parser.add_argument('--model', default='gpt-4', help='LLM model for token estimation (default: gpt-4)')
    parser.add_argument('--timeout', type=float, default=30.0, help='Request timeout in seconds (default: 30)')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--save', metavar='FILE', help='Save output to file')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    
    args = parser.parse_args()
    
    # Determine strict mode
    strict = args.strict and not args.permissive
    
    # Determine mode
    mode = "render" if args.render else "fast"
    
    try:
        # Ingest content
        doc = ingest(
            url=args.url,
            mode=mode,
            strict=strict,
            model=args.model,
            timeout=args.timeout
        )
        
        # Output handling
        if args.json:
            output = _format_json_output(doc)
            if args.save:
                Path(args.save).write_text(output)
                print(f"✔ Saved JSON to {args.save}", file=sys.stderr)
            else:
                print(output)
        else:
            # Default: pretty terminal output
            _print_summary(doc)
            
            if args.save:
                Path(args.save).write_text(doc.markdown)
                print(f"\n✔ Saved Markdown to {args.save}")
            else:
                print("\n" + "="*60)
                print("MARKDOWN OUTPUT")
                print("="*60 + "\n")
                print(doc.markdown)
    
    except KeyboardInterrupt:
        print("\n✗ Interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


def _print_summary(doc):
    """Print pretty summary to terminal"""
    scorer = Scorer()
    risk_level = scorer.get_risk_level(doc.injection_score)
    
    # Color codes for risk levels
    risk_colors = {
        'safe': '\033[92m',      # Green
        'low': '\033[93m',       # Yellow
        'medium': '\033[93m',    # Yellow
        'high': '\033[91m',      # Red
        'critical': '\033[91m',  # Red
    }
    reset = '\033[0m'
    color = risk_colors.get(risk_level, '')
    
    print(f"\n{'='*60}")
    print(f"MarkDownIngress v{__version__} - Ingestion Report")
    print(f"{'='*60}")
    print(f"\n📄 Title: {doc.metadata.get('title', 'N/A')}")
    print(f"🔗 URL: {doc.metadata['url']}")
    print(f"\n✔ Tokens: {doc.token_estimate:,}")
    
    savings = doc.metadata.get('token_savings', {})
    if savings:
        print(f"  ↳ Saved: {savings.get('saved_tokens', 0):,} tokens ({savings.get('savings_percent', 0)}% reduction)")
    
    print(f"\n🔒 Injection Score: {color}{doc.injection_score:.3f}{reset} ({risk_level.upper()})")
    
    if doc.flags:
        print(f"⚠️  Flags: {', '.join(doc.flags)}")
    
    removed = doc.removed_elements
    if removed.get('tags'):
        tag_summary = ', '.join(f"{k}:{v}" for k, v in removed['tags'].items())
        print(f"\n🗑️  Removed tags: {tag_summary}")
    
    if removed.get('hidden_elements', 0) > 0:
        print(f"🗑️  Removed hidden elements: {removed['hidden_elements']}")
    
    print(f"\n🔑 Hash: {doc.content_hash}")
    print(f"⏱️  Fetch time: {doc.metadata.get('fetch_time_ms', 0):.0f}ms")


def _format_json_output(doc) -> str:
    """Format SafeDocument as JSON"""
    output = {
        'markdown': doc.markdown,
        'metadata': doc.metadata,
        'token_estimate': doc.token_estimate,
        'content_hash': doc.content_hash,
        'injection_score': doc.injection_score,
        'flags': doc.flags,
        'removed_elements': doc.removed_elements,
    }
    
    return json.dumps(output, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    main()
