from web3_analysis import source_model


def test_internal_chain_propagates_effects_without_unreachable_contamination(tmp_path):
    (tmp_path / 'Vault.sol').write_text('''contract Vault {
        uint256 total;
        function withdraw() external { debit(); }
        function debit() internal { total -= 1; payout(); }
        function payout() internal { token.transfer(msg.sender, 1); debit(); }
        function unrelated() internal { rogue.call(""); }
        function safe() external view returns(uint256) { return total; }
    }''')
    model = source_model(tmp_path)
    entries = {f['name']: f for f in model['entrypoints']}
    assert entries['withdraw']['writes'] == ['total']
    assert entries['withdraw']['external_calls'] == ['token.transfer']
    assert entries['withdraw']['reachable_internal_functions'] == ['debit', 'payout']
    assert entries['safe']['writes'] == []
    assert [p['entrypoint'] for p in model['risk_paths']] == ['Vault.withdraw()']


def test_overloads_are_unresolved_and_abstract_declarations_do_not_steal_body(tmp_path):
    (tmp_path / 'Vault.sol').write_text('''abstract contract Vault {
        uint256 total;
        function abstractEntry() external virtual;
        function entry() external { helper(1); }
        function helper(uint256 x) internal { total = x; }
        function helper(address x) internal { token.transfer(x, 1); }
        function remote() external { token.helper(1); }
    }''')
    entries = {f['name']: f for f in source_model(tmp_path)['entrypoints']}
    assert entries['abstractEntry']['writes'] == []
    assert entries['abstractEntry']['internal_calls'] == []
    assert entries['entry']['unresolved_internal_calls'] == ['helper']
    assert entries['entry']['writes'] == []
    assert entries['remote']['internal_calls'] == []
