# Mares Data

Arquivos estaticos, imutaveis e versionados para o aplicativo Garmin Marés.
Eles nao contem contas de usuario, credenciais ou coordenadas precisas de quem
consulta os dados.

## Estrutura

- `current.json`: unico ponteiro mutavel para a ultima release valida;
- `releases/<id>/catalog`: estacoes aprovadas e metadados de fonte;
- `releases/<id>/forecast`: extremos por estacao; o cliente global usa
  `next-30.json`, uma unica janela rolante de 30 dias;
- `releases/<id>/release.json`: metadados e integridade da release.

Cada pacote inclui fonte, datum, fuso, classe da previsao, validade e hash.
O relogio valida tudo antes de substituir o ultimo cache valido. Ele baixa
somente a previsao da estacao ativa; nao existe acervo nacional no relogio.

## Fontes e uso

Os dados sao previsoes astronomicas. Nao servem para navegacao, seguranca,
onda, corrente, vento, pressao ou storm surge. Ver a atribuicao por estacao
antes de reutilizar qualquer arquivo.
