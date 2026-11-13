#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'

root = File.expand_path('../..', __dir__)
header, *lines = File.readlines(File.join(root, 'eval/ruby-gold.tsv'), chomp: true)
gold = lines.map { |line| header.split("\t").zip(line.split("\t")).to_h }
models = %w[30 50 100 Paddle]
correct = models.to_h { |model| [model, 0] }

def overlap_ratio(a, b)
  width = [[a[2], b[2]].min - [a[0], b[0]].max, 0].max
  height = [[a[3], b[3]].min - [a[1], b[1]].max, 0].max
  areas = [a, b].map { |box| (box[2] - box[0]) * (box[3] - box[1]) }
  width * height.to_f / areas.min
end

puts "page  parent  expected  30  50  100  Paddle"
gold.each do |row|
  page = format('%03d', row.fetch('page').to_i)
  ruby_data = JSON.parse(File.read(File.join(root, "work/ruby/page-#{page}.json")))
  ruby = ruby_data.fetch('rubies').find do |item|
    item.fetch('box')[0] == row.fetch('x').to_i && item.fetch('box')[1] == row.fetch('y').to_i
  end
  abort "ruby block missing: page #{page}, x=#{row['x']}, y=#{row['y']}" unless ruby

  paddle_data = JSON.parse(File.read(File.join(root, "work/paddle/page-#{page}/page-#{page}-000_res.json")))
  candidates = paddle_data.fetch('rec_boxes').each_with_index.map do |box, index|
    ruby_box = ruby.fetch('box')
    next if box[2] - box[0] > 2 * (ruby_box[2] - ruby_box[0])
    next if box[3] - box[1] > 2 * (ruby_box[3] - ruby_box[1])

    [overlap_ratio(ruby_box, box), paddle_data.fetch('rec_texts').fetch(index)]
  end.compact
  best = candidates.max_by(&:first)
  paddle_text = best && best.first >= 0.5 ? best.last : ''
  predictions = ruby.fetch('text').merge('Paddle' => paddle_text)
  models.each { |model| correct[model] += 1 if predictions.fetch(model) == row.fetch('expected') }
  puts ([page, row.fetch('parent'), row.fetch('expected')] + models.map do |model|
    predictions.fetch(model).empty? ? '(未検出)' : predictions.fetch(model)
  end).join('  ')
end

puts "exact match: #{models.map { |model| "#{model}=#{correct.fetch(model)}/#{gold.length}" }.join('  ')}"
puts '画像で正解を確認した3件だけの結果。全ページの精度は示さない。'
